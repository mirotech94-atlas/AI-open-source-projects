import type { HTMLAttributes, ReactNode } from "react";
import type { Tone } from "../lib/format";

export type BadgeProps = {
  tone?: Tone;
  children?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLSpanElement>, "children">;

/** A static label. Deliberately not focusable — it is text, not a control. */
export function Badge({ tone = "plain", children, className = "", ...rest }: BadgeProps) {
  return (
    <span className={`g-badge g-tone-${tone} ${className}`.trim()} {...rest}>
      {children}
    </span>
  );
}

export default Badge;
