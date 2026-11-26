"""Collect errors from parameter diagnostics that report advisory messages."""


def advise(warnings, check, *args, **kwargs):
    try:
        return check(*args, **kwargs)
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        warnings.append(str(exc))
