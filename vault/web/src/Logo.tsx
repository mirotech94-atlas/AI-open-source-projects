type LogoProps = {
  size?: number;
};

/** The Vault mark: a vault door seen face-on -- its rim, two hinges, and the
 *  combination dial at its centre. This is the compact cut
 *  (`docs/brand/vault-mark-compact.svg`) for the top bar's 24-28 px; the full
 *  mark adds bolt heads and dial ticks that would blur at this size.
 *
 *  The blue is the brand blue `#1257c4` itself, not `--color-primary`: on the
 *  dark skin `--color-primary` lightens to the interactive `#66a6ff`, and a
 *  mark that changes colour with the theme stops being the same mark people
 *  see on the site, in email and on LinkedIn. `#1257c4` is the one value the
 *  site and portal share exactly (CLAUDE.md, Design assets). */
export default function Logo({ size = 28 }: LogoProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="Vault">
      <circle cx="24" cy="24" r="24" fill="#1257c4" />
      {/* hinges */}
      <rect x="4.6" y="15.6" width="6.4" height="4.6" rx="1.3" fill="#fff" />
      <rect x="4.6" y="27.8" width="6.4" height="4.6" rx="1.3" fill="#fff" />
      {/* the door: rim, a blue gap, then the face */}
      <circle cx="24" cy="24" r="16.2" fill="#fff" />
      <circle cx="24" cy="24" r="14.1" fill="#1257c4" />
      <circle cx="24" cy="24" r="12.7" fill="#fff" />
      {/* the combination dial and its index mark */}
      <circle cx="24" cy="24" r="6.4" fill="#1257c4" />
      <circle cx="24" cy="24" r="2.3" fill="#fff" />
      <line x1="24" y1="18.9" x2="24" y2="20.4" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
