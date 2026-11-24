import type { JSX } from "react";
import { CPanel, Unread } from "./cmdParts";
import { hhmm } from "./homeModel";
import { safeText } from "./retired";
import type { TokenReading, YouTubeReading } from "./youtubeModel";

/**
 * Atlas Academy · YouTube. Pure: it draws the readings it is given. An
 * unreadable channel is Unknown with its reason, never a number; a figure the
 * channel did not report says "unreported", never 0; each figure carries the
 * time its check read it.
 */
function readAt(iso: string | null): string {
  return iso ? `read ${hhmm(iso)}` : "no read time";
}

function Figure({ label, value, at }: { label: string; value: number | null; at: string | null }): JSX.Element {
  return (
    <li>
      <span className="k">{label}</span>
      {value === null
        ? <span className="v cmd-word-unknown">unreported</span>
        : <span className="v">{value.toLocaleString("en-US")}</span>}
      <span className="s">{readAt(at)}</span>
    </li>
  );
}

export function YouTubePanel({ reading, token, at }: {
  reading: YouTubeReading;
  token: TokenReading | null;
  /** The panel's own read line: `stamp(...)`. */
  at: string;
}): JSX.Element {
  return (
    <CPanel id="youtube" title="Atlas Academy · YouTube" at={at}>
      <p className="cmd-note cmd-yt-sub">@atlasacademyphp</p>
      {reading.state === "unknown" ? (
        <Unread>{reading.why}</Unread>
      ) : reading.state === "ok-nonumbers" ? (
        <p className="cmd-empty" data-health="ok">{reading.text} <small>{readAt(reading.readAt)}</small></p>
      ) : (
        <ul className="cmd-yt" data-health="ok">
          <Figure label="Subscribers" value={reading.subscribers} at={reading.readAt} />
          <Figure label="Views" value={reading.views} at={reading.readAt} />
          {reading.latestVideos === null
            ? <li><span className="k">Recent videos</span>
                <span className="v cmd-word-unknown">not read</span></li>
            : <Figure label="Recent videos" value={reading.latestVideos} at={reading.latestReadAt} />}
        </ul>
      )}
      <p className="cmd-yt-token" data-health={token ? token.health : "unknown"}>
        <span className="k">Token</span>{" "}
        {token === null ? <span className="cmd-word-unknown">Unknown</span>
          : token.health === "unknown" ? <span className="cmd-word-unknown">Unknown</span>
          : <span>{token.health === "ok" ? "OK" : token.health}</span>}
        {token && token.detail ? <span> · {safeText(token.detail)}</span> : null}
        <small> · {token ? readAt(token.readAt) : "token check not reporting"}</small>
      </p>
    </CPanel>
  );
}
