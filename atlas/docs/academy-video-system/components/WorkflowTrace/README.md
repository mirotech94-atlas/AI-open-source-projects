The data-movement preset: a single labeled token travels one connector between two real nodes and stops when it arrives, with the input and output values shown beneath. It is how the learner understands the transformation.

**The editor supplies:** the two node names and their real values (copied from the executed workflow), the token label (`value-tag` style, mono, one short key/value), and the frame the trace starts.

**Behaviour:** the connector fills `blue` and the token moves along it over `trace` (12–18 frames) on `settle`; both stop at the destination node, whose border turns `success` only if that step ran. State is labelled with text as well as colour.

| Do | Don't |
|---|---|
| Trace one connector per beat | Light up the whole canvas at once |
| Show the real input and output values | Animate a token into a node that never executed |
| Cut to the actual node output right after | Loop the trace while narration continues |
