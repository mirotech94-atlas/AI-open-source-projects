// ATLAS's lines in the standalone Air Glass: captions only.
//
// the owner wants ATLAS to speak only in his chosen Fish Audio voice. This page
// runs on 127.0.0.1 with no ATLAS session, so it can reach neither Fish
// Audio nor ATLAS's offline pack of Fish clips. It does not use the
// browser's own voices (the Mac's system voices) either: every line ATLAS
// would say is shown as a caption, and nothing is spoken. The Air Glass
// inside ATLAS (/airglass) speaks in the Fish voice, offline included.

export class Voice {
  constructor() {
    this.enabled = true;
    this.voice = null;         // no voice here, by design
    this.lastError = null;
    this.started = 0;          // stays 0: nothing is ever spoken
    this.onChange = null;
  }

  get available() { return false; }

  get label() { return 'captions only (ATLAS speaks only in its Fish voice)'; }

  /** Never speaks. Returns false so the caller knows the line is caption only. */
  say(_text) { return false; }
}
