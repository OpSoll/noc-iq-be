def collect_multisig(signers, threshold):
    """Return True if distinct signers' weights meet threshold.

    ``signers`` is an iterable of (signer_key, weight); duplicate keys are
    counted once (first weight wins) so a signer cannot be repeated to reach
    the threshold.
    """
    unique = {}
    for key, weight in signers:
        unique.setdefault(key, weight)
    return sum(unique.values()) >= threshold
