import { Stat } from "../design";
import { stateTone } from "../lib/format";
import type { ExposureTile } from "../types";

export type ExposureTilesProps = {
  tiles: ExposureTile[];
};

/** What the outside world can see, as a row of `Stat` cards toned from each
 *  tile's own `state`. */
export function ExposureTiles({ tiles }: ExposureTilesProps) {
  if (tiles.length === 0) {
    return <p className="g-asset-empty">No exposure data yet.</p>;
  }

  return (
    <div className="g-sec-exposure">
      {tiles.map((tile) => (
        <Stat
          key={tile.label}
          label={tile.label}
          value={tile.value}
          sub={tile.detail}
          tone={stateTone(tile.state)}
          data-testid="exposure-tile"
        />
      ))}
    </div>
  );
}

export default ExposureTiles;
