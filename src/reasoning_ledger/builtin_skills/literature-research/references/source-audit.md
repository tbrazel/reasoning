# Scholarly source audit

## Identity

- Exact DOI and canonical `https://doi.org/...` URL
- Exact arXiv identifier including version when the version matters
- Full author list, title, venue, and publication date
- Relationship between preprint, accepted manuscript, and version of record

## Status

- Crossref `relation` and `update-to` metadata
- Corrections, expressions of concern, withdrawals, or retractions
- arXiv submitted and last-updated timestamps
- Journal reference and DOI deposited on the arXiv entry
- License applying to the specific version or linked content

## Evidentiary use

- Exact page, section, theorem, proposition, figure, or dataset supporting the claim
- Exact versioned source-archive hash and member path/hash when evidence comes from an arXiv attachment
- Whether the source proves, computes, observes, conjectures, or merely cites it
- Assumptions, scope, and version-specific caveats
- Independent corroborating source when the claim is central or surprising

Crossref's public REST API returns publisher- and source-deposited metadata.
Almost all bibliographic metadata can be reused, but abstracts and linked content
may have separate copyright. arXiv API records expose versioned metadata and
item-specific licenses; they do not make every linked work freely reusable.

Official references:

- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Crossref access: https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/
- arXiv API terms: https://info.arxiv.org/help/api/tou.html
- arXiv API manual: https://info.arxiv.org/help/api/user-manual.html
