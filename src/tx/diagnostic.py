def filter_diagnostic_events(events, debug=False):
    """Redact XDR payloads and mask account identifiers unless ``debug``."""
    if debug:
        return events
    out = []
    for event in events:
        e = dict(event)
        for k, v in e.items():
            if "xdr" in k.lower():
                e[k] = "[REDACTED]"
            elif "account" in k.lower() and isinstance(v, str) and len(v) > 8:
                e[k] = v[:4] + "..." + v[-4:]
        out.append(e)
    return out
