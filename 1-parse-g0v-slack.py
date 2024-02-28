import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta

def scrape_messages():
    url = "https://g0v-slack-archive.g0v.ronny.tw/"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Your parsing logic here. This is a placeholder.
    messages = [{"message": "Hello, world!", "timestamp": str(datetime.now())}]
    
    # Save to JSON
    with open('messages.json', 'w') as json_file:
        json.dump(messages, json_file)

if __name__ == "__main__":
    scrape_messages()
