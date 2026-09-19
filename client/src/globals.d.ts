// Shared ambient declarations. These files are classic scripts, so anything one
// script exposes to another travels through the global object or a custom event.

interface SpringOptions {
  from: number;
  to: number;
  velocity?: number;
  onUpdate: (value: number, velocity: number) => void;
  onComplete?: () => void;
}

interface SpringHandle {
  cancel: () => void;
}

/** Centre, reach and length of a theme reveal, in viewport pixels and ms. */
interface ThemeRevealDetail {
  x: number;
  y: number;
  radius: number;
  duration: number;
}

interface LanguageSettledDetail {
  animate?: boolean;
}

// Senders build these details at runtime, so a listener must treat every field
// as missing until it has checked. That check is what the code already does.
interface DocumentEventMap {
  'rep0rter:theme-reveal': CustomEvent<Partial<ThemeRevealDetail> | null>;
  'rep0rter:theme-cancel': CustomEvent<unknown>;
  'rep0rter:before-language': CustomEvent<unknown>;
  'rep0rter:language-settled': CustomEvent<LanguageSettledDetail | null>;
}

interface Window {
  /** Shared by the theme reveal and the glyph layer; either may finish first. */
  Rep0rterScrollLock?: Readonly<{
    acquire: () => () => void;
    isLocked: () => boolean;
    isScrollKey: (event: KeyboardEvent) => boolean;
  }>;
  Rep0rterMotion?: { spring: (options: SpringOptions) => SpringHandle };
  /** Swaps light/dark artwork; resolves once visible images have decoded. */
  Rep0rterImages?: { sync: () => Promise<void> | undefined };
  Rep0rterLanguage?: Readonly<{
    change: (locale: string | undefined,
             options?: { initial?: boolean; history?: boolean }) => Promise<void>;
    publicURL: (value: string, locale?: string) => URL;
  }>;
  Rep0rterEnchantment?: Readonly<{
    play: (element?: HTMLElement, reveal?: ThemeRevealDetail | null) => () => void;
    cancel: () => void;
    playAll: () => void;
  }>;
}
