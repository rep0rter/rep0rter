"""Bounded public HTTPS reads for sources configured by signed-in owners.

Resolve and pin public IPs on every redirect to prevent local-network requests
and DNS rebinding. No cookies, environment proxies or credentials are forwarded.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3

MAX_SOURCE_BYTES = 2_000_000


def public_address(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError('Source must use public HTTPS on port 443.')
    hostname = parsed.hostname.encode('idna').decode('ascii')
    addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or not all(ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Source must resolve only to public addresses.')
    return hostname, addresses[0][4][0]


def fetch(url):
    from .runtime import services
    if services.get() is not None:
        return services.get().public_fetch(url, MAX_SOURCE_BYTES)
    deadline = time.monotonic() + 25
    for _ in range(4):
        hostname, address = public_address(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Source request timed out.')
        parsed = urlsplit(url)
        target = (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
        with urllib3.HTTPSConnectionPool(
            address, port=443, server_hostname=hostname, assert_hostname=hostname,
            cert_reqs=ssl.CERT_REQUIRED, ca_certs=certifi.where(), retries=False,
            timeout=urllib3.Timeout(total=remaining, connect=min(3, remaining), read=min(6, remaining)),
        ) as pool:
            response = pool.urlopen('GET', target, redirect=False, retries=False,
                                    preload_content=False, decode_content=False,
                                    headers={'Host': f'[{hostname}]' if ':' in hostname else hostname, 'Accept-Encoding': 'identity',
                                             'User-Agent': 'rep0rter-project-news/1.0'})
            try:
                if response.status in (301, 302, 303, 307, 308):
                    if not response.headers.get('Location'):
                        raise ValueError('Invalid source redirect.')
                    url = urljoin(url, response.headers['Location'])
                    continue
                if response.status != 200:
                    raise ValueError('Source returned an unsuccessful response.')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('Compressed source response is unsupported.')
                body = bytearray()
                while len(body) <= MAX_SOURCE_BYTES:
                    if time.monotonic() >= deadline:
                        raise ValueError('Source request timed out.')
                    chunk = response.read1(min(32768, MAX_SOURCE_BYTES + 1 - len(body)), decode_content=False)
                    if not chunk:
                        return bytes(body)
                    body.extend(chunk)
                raise ValueError('Source exceeds the two megabyte limit.')
            finally:
                response.close()
    raise ValueError('Too many source redirects.')
