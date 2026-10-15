# LMFDB query and interpretation notes

LMFDB's simple API lives at `https://www.lmfdb.org/api/`. Typed query values
use prefixes such as `i` for integers and `s` for strings. The API supports
field selection, sorting, and offsets, returns at most 100 records per page,
and limits a query to roughly 10,000 results. Reasoning exposes only a curated
subset of exact filters and fields; the response retains the exact query URL,
raw-response hash, provider page information, and direct object links.

For elliptic curves over the rationals, an LMFDB label such as `11.a1`
identifies one curve, while `11.a` identifies its isogeny class. The returned
`ainvs` specify the database model. Distinguish `rank` from `analytic_rank`,
and consult the object's page before relying on `sha`, generators, optimality,
or completeness of a search.

Number-field labels encode degree, signature data, absolute discriminant, and
an index. Coefficient arrays are in ascending order. Preserve `used_grh` and
do not treat a defining polynomial as a canonical presentation of the field.

The simple API is appropriate for small, reproducible lookups. LMFDB also
links a read-only MCP service at `https://mcp.lmfdb.org/mcp` for broader table
discovery and analysis. Arbitrary SQL through that service requires separate
review even though the mirror is read-only: bound rows, select explicit
columns, and preserve the executed query.

Official references:

- API and typed-query format: https://www.lmfdb.org/api/
- Access methods and limits: https://www.lmfdb.org/api/options
- License: https://www.lmfdb.org/license
- Elliptic curves over Q source notes: https://www.lmfdb.org/EllipticCurve/Q/Source
