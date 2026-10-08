import type { HTMLAttributes } from "react";
import { Badge } from "../design";
import { stateTone } from "../lib/format";
import type { QuestionItem } from "../types";

export type QuestionStripProps = {
  items: QuestionItem[];
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

/** The five questions ("Am I visible to attackers?", etc.) as a compact strip
 *  under the hero: question, answer, tone. Pure. */
export function QuestionStrip({ items, className = "", ...rest }: QuestionStripProps) {
  if (items.length === 0) return null;
  return (
    <div className={`g-question-strip ${className}`.trim()} {...rest}>
      {items.map((item) => (
        <div key={item.question} data-testid="question" className="g-question">
          <span className="g-question-text">{item.question}</span>
          <Badge tone={stateTone(item.state)}>{item.answer}</Badge>
        </div>
      ))}
    </div>
  );
}

export default QuestionStrip;
