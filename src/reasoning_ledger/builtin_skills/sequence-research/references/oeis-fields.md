# OEIS fields and interpretation

The official JSON endpoint is `https://oeis.org/search?q=QUERY&fmt=json`.
An exact identifier lookup uses an `id:A000000` query. The `data` field is a
comma-separated sequence prefix; Reasoning normalizes it to integers while
preserving the response hash and exact request URL.

The `offset` field commonly contains two integers. The first is the index of
the first listed term. The second identifies the first term whose absolute
value is greater than one, or the number of terms when no such term occurs.
Do not confuse the second value with another starting index.

Important entry fields include:

- `name`: a concise identification, not a proof;
- `comment` and `formula`: contributor-supplied mathematical context;
- `reference` and `link`: literature and external supporting material;
- `xref`: related or transformed sequences;
- `program`: implementations in one or more languages;
- `keyword`: entry classification and warnings;
- `author`: submission or edit attribution.

A b-file at `https://oeis.org/A000000/b000000.txt` contains explicit
`index value` pairs. Comments begin with `#`. Always retain the listed indices,
because the first one need not be zero or one and exceptional terms can matter.

OEIS content is offered under CC BY-SA 4.0 subject to the OEIS End-User License
Agreement. Attribute the On-Line Encyclopedia of Integer Sequences and link to
the exact A-number. Consult the official references when reuse goes beyond a
short research citation:

- JSON format: https://oeis.org/wiki/JSON_Format
- Download and licensing: https://oeis.org/wiki/Download
- End-User License Agreement: https://oeis.org/wiki/The_OEIS_End-User_License_Agreement
