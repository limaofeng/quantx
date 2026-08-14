const MARKET_OPEN_MINUTES = 9 * 60 + 30;
const MORNING_CLOSE_MINUTES = 11 * 60 + 30;
const AFTERNOON_OPEN_MINUTES = 13 * 60;
const MARKET_CLOSE_MINUTES = 15 * 60;
const MORNING_SESSION_LENGTH = MORNING_CLOSE_MINUTES - MARKET_OPEN_MINUTES;

const shanghaiTimeFormatter = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  timeZone: 'Asia/Shanghai',
});

const getShanghaiMinutes = (date: Date) => {
  let hour = 0;
  let minute = 0;

  shanghaiTimeFormatter.formatToParts(date).forEach(part => {
    if (part.type === 'hour') {
      hour = Number(part.value === '24' ? '0' : part.value);
    }
    if (part.type === 'minute') minute = Number(part.value);
  });

  return hour * 60 + minute;
};

export const MARKET_SESSION_MINUTES = 240;
export const MARKET_SESSION_TICKS = [0, 60, 120, 180, 240] as const;

/**
 * Convert real Shanghai clock time into elapsed A-share trading minutes.
 * The 11:30-13:00 lunch break is removed from the coordinate system.
 */
export function toMarketSessionMinute(date: Date): number | null {
  const minutes = getShanghaiMinutes(date);

  if (minutes < MARKET_OPEN_MINUTES || minutes > MARKET_CLOSE_MINUTES) {
    return null;
  }
  if (minutes > MORNING_CLOSE_MINUTES && minutes < AFTERNOON_OPEN_MINUTES) {
    return null;
  }
  if (minutes <= MORNING_CLOSE_MINUTES) {
    return minutes - MARKET_OPEN_MINUTES;
  }

  return MORNING_SESSION_LENGTH + (minutes - AFTERNOON_OPEN_MINUTES);
}

export function formatMarketSessionMinute(value: number): string {
  const rounded = Math.max(
    0,
    Math.min(MARKET_SESSION_MINUTES, Math.round(value))
  );

  if (rounded === MORNING_SESSION_LENGTH) return '11:30 / 13:00';

  const clockMinutes =
    rounded < MORNING_SESSION_LENGTH
      ? MARKET_OPEN_MINUTES + rounded
      : AFTERNOON_OPEN_MINUTES + (rounded - MORNING_SESSION_LENGTH);
  const hour = Math.floor(clockMinutes / 60);
  const minute = clockMinutes % 60;
  return `${hour.toString().padStart(2, '0')}:${minute
    .toString()
    .padStart(2, '0')}`;
}
