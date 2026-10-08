type ChatMarkProps = {
  size?: number;
  /** White bubble with the core in the button's own colour — for use ON the
   *  blue floating chat button. Default is the solid primary bubble with a
   *  white core, for the chat panel header. */
  inverted?: boolean;
};

/** The Vault chat mark: the same hexagonal AI core as the shield, inside a
 *  speech bubble — talk to the intelligence that guards you. Decorative on
 *  purpose: the button and the chat dialog that carry it already have the
 *  accessible name. */
export default function ChatMark({ size = 24, inverted = false }: ChatMarkProps) {
  const bubble = inverted ? "#ffffff" : "var(--color-primary)";
  const ink = inverted ? "var(--color-primary)" : "#ffffff";
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      {/* speech bubble with a tail at bottom-left */}
      <path
        d="M10 7 H38 A6 6 0 0 1 44 13 V31 A6 6 0 0 1 38 37 H22 L13 45 V37 H10 A6 6 0 0 1 4 31 V13 A6 6 0 0 1 10 7 Z"
        fill={bubble}
      />
      {/* hexagonal AI core */}
      <path
        d="M24 12.5 L31.8 17 V26 L24 30.5 L16.2 26 V17 Z"
        fill="none"
        stroke={ink}
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="21.5" r="3.2" fill={ink} />
      {/* live node at the apex */}
      <circle cx="24" cy="12.5" r="2.3" fill="#6ee7a0" />
    </svg>
  );
}
