/**
 * The ATLAS mark.
 *
 * A load resting in the apex of a bearing frame. It is the letter A, and it
 * is the structure underneath — which is what this system is: a kernel that
 * knows nothing about flycheap or Guardian, holding them up anyway.
 *
 * Three earlier attempts are worth not repeating. A bottom arc under a
 * centred dot reads as a smiley face at any size above about 48px. Radial
 * ticks around a core — however carefully the spacing was varied — read as
 * a sun or a loading spinner, which is the default every monitoring product
 * arrives at. Uneven tick radii mud into a smudge by 16px.
 *
 * `currentColor` throughout, so it inherits --cyan in the sidebar and the
 * state colour anywhere a health tint applies.
 */
export function Mark({ size = 26 }: { size?: number }): JSX.Element {
  return (
    <svg
      viewBox="0 0 32 32" width={size} height={size} fill="none"
      stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round"
      role="img" aria-label="ATLAS"
    >
      {/* The load. Filled, so it is the one solid thing in the mark. */}
      <circle cx="16" cy="7.6" r="3.4" fill="currentColor" stroke="none" />
      {/* Two legs, meeting under the load rather than touching it. */}
      <path d="M4.6 26.6L12.4 12.2" />
      <path d="M27.4 26.6L19.6 12.2" />
      {/* The span that makes it an A and stops the legs splaying into a
          drafting compass. */}
      <path d="M8.2 19.8H23.8" />
    </svg>
  );
}
