"""Plain terminal labels from external text."""
import unicodedata


def plain(value):
    return ''.join(c for c in str(value or '')
                   if c in '\n\t' or not unicodedata.category(c).startswith('C'))
