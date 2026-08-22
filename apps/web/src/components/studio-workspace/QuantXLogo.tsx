export function QuantXLogo() {
  return (
    <svg
      aria-hidden="true"
      data-testid="studio-brand-logo"
      style={{
        filter: 'drop-shadow(0 1px 2px rgba(0, 0, 0, 0.45))',
        flexShrink: 0,
        height: 30,
        width: 30,
      }}
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id="quantx-logo-surface"
          x1="6"
          x2="26"
          y1="4"
          y2="28"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#20334c" />
          <stop offset="1" stopColor="#0b1524" />
        </linearGradient>
        <linearGradient
          id="quantx-logo-accent"
          x1="17"
          x2="25"
          y1="9"
          y2="23"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#F8FAFC" />
          <stop offset="1" stopColor="#8FA7C2" />
        </linearGradient>
      </defs>

      <circle
        cx="16"
        cy="16"
        r="15"
        fill="url(#quantx-logo-surface)"
        stroke="#53677F"
        strokeWidth="0.85"
      />
      <path
        d="M5.8 9.3A13.1 13.1 0 0 1 25.5 6.8"
        fill="none"
        opacity="0.18"
        stroke="#FFFFFF"
        strokeLinecap="round"
      />
      <circle
        cx="11.8"
        cy="15.6"
        r="5.75"
        fill="none"
        stroke="#F8FAFC"
        strokeWidth="1.85"
      />
      <path
        d="m15.6 19.8 3.1 3.1"
        fill="none"
        stroke="#F8FAFC"
        strokeLinecap="round"
        strokeWidth="1.85"
      />
      <path
        d="m18.2 9.8 7.1 12.1m0-12.1-7.1 12.1"
        fill="none"
        stroke="url(#quantx-logo-accent)"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}
