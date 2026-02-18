"""Realistic sample Crossref API responses for testing."""

# A full work message (the "message" field from GET /works/{doi})
SAMPLE_CROSSREF_WORK = {
    "DOI": "10.1145/3230543.3230563",
    "type": "proceedings-article",
    "title": ["Restructuring endpoint congestion control"],
    "container-title": ["Proceedings of the 2018 Conference of the ACM Special Interest Group on Data Communication"],
    "publisher": "Association for Computing Machinery (ACM)",
    "ISSN": ["0146-4833"],
    "issued": {
        "date-parts": [[2018, 8, 7]],
    },
    "is-referenced-by-count": 92,
    "author": [
        {"given": "Akshay", "family": "Narayan", "sequence": "first"},
        {"given": "Frank", "family": "Cangialosi", "sequence": "additional"},
    ],
    "abstract": "<p>We present a new approach to congestion control.</p>",
    "reference-count": 45,
    "URL": "https://doi.org/10.1145/3230543.3230563",
}

# Full API response wrapping the message
SAMPLE_CROSSREF_RESPONSE = {
    "status": "ok",
    "message-type": "work",
    "message": SAMPLE_CROSSREF_WORK,
}

# A journal article
SAMPLE_CROSSREF_JOURNAL_WORK = {
    "DOI": "10.1109/TNET.2020.3027697",
    "type": "journal-article",
    "title": ["Understanding Modern Network Traffic"],
    "container-title": ["IEEE/ACM Transactions on Networking"],
    "publisher": "Institute of Electrical and Electronics Engineers (IEEE)",
    "ISSN": ["1063-6692", "1558-2566"],
    "issued": {
        "date-parts": [[2021, 2]],
    },
    "is-referenced-by-count": 30,
    "author": [
        {"given": "Jane", "family": "Researcher", "sequence": "first"},
    ],
}

SAMPLE_CROSSREF_JOURNAL_RESPONSE = {
    "status": "ok",
    "message-type": "work",
    "message": SAMPLE_CROSSREF_JOURNAL_WORK,
}

# Minimal work (missing optional fields)
SAMPLE_CROSSREF_MINIMAL_WORK = {
    "DOI": "10.9999/minimal.2023",
    "type": "other",
    "title": ["A Minimal Entry"],
    "issued": {
        "date-parts": [[2023]],
    },
    "is-referenced-by-count": 0,
}

SAMPLE_CROSSREF_MINIMAL_RESPONSE = {
    "status": "ok",
    "message-type": "work",
    "message": SAMPLE_CROSSREF_MINIMAL_WORK,
}
