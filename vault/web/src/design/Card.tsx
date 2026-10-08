import type { HTMLAttributes, ReactNode } from "react";
import type { Tone } from "../lib/format";

export type CardProps = {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Sets the left border only, so a coloured card still reads as a card. */
  tone?: Tone;
  children?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLElement>, "title" | "children">;

export function Card({
  title,
  subtitle,
  actions,
  tone = "plain",
  children,
  className = "",
  ...rest
}: CardProps) {
  const hasHead = title !== undefined || subtitle !== undefined || actions !== undefined;
  return (
    <section className={`g-card g-tone-${tone} ${className}`.trim()} {...rest}>
      {hasHead && (
        <div className="g-card-head">
          <div>
            {title !== undefined && <h2 className="g-card-title">{title}</h2>}
            {subtitle !== undefined && <p className="g-card-subtitle">{subtitle}</p>}
          </div>
          {actions !== undefined && <div className="g-card-actions">{actions}</div>}
        </div>
      )}
      {children !== undefined && <div className="g-card-body">{children}</div>}
    </section>
  );
}

export default Card;
