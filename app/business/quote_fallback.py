"""Conservative Vietnamese quotation facts when the semantic provider is unavailable.

Only field labels and quantities are normalized. Names are sliced from the original
utterance so accents, punctuation and product codes survive Odoo lookup.
"""
import re
import time
import unicodedata
from datetime import date, timedelta

from app.business.quote_local import requests_fresh_quote


NUMBER_WORDS = {'mot': 1, 'hai': 2, 'ba': 3, 'bon': 4, 'nam': 5,
                'sau': 6, 'bay': 7, 'tam': 8, 'chin': 9, 'muoi': 10}
UNITS = r'(?:cai|chiec|bo|hop|san pham)'
ACTION = r'(?:tao|lap|len|soan)'
POLITE = r'(?:giup|cho|em|minh|toi|anh|chi|nhe|a|voi|mot|1|moi)'


def plain(text):
    normalized = unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')


def normalized_with_map(text):
    """Return normalized characters and offsets into the original text."""
    result, offsets = [], []
    for index, char in enumerate(text):
        for normalized in plain(char):
            result.append(normalized)
            offsets.append(index)
    return ''.join(result), offsets


def original_slice(text, offsets, start, end):
    return text[offsets[start]:offsets[end - 1] + 1].strip(' ,;.!?') if end > start else ''


def pending_field(session, draft=None):
    if (draft or {}).get('quotation_hints', {}).get('unclassified_product_candidate'):
        return 'product_candidate_confirmation'
    selection = (draft or {}).get('quotation_hints', {}).get('selection') or {}
    if selection.get('kind') and time.time() - selection.get('at', 0) <= 1800:
        return selection['kind']
    if (draft or {}).get('quotation_hints', {}).get('product_query_1_needs_confirmation'):
        return 'product'
    last = next((str(row.get('content') or '') for row in reversed((session or {}).get('history') or [])
                 if row.get('role') == 'assistant'), '')
    last = plain(last)
    matched = [field for phrase, field in (
        ('dieu khoan thanh toan', 'payment_term'), ('het han', 'validity_date'),
        ('bang gia', 'pricelist'), ('khach', 'customer'),
        ('san pham', 'product')) if phrase in last]
    return matched[0] if len(matched) == 1 else None


def explicit_customer_name(text):
    normalized, offsets = normalized_with_map(text)
    match = re.search(r'\b(?:ten khach hang|khach hang|khach)\s*:\s*([^,;\n]+)', normalized)
    return original_slice(text, offsets, *match.span(1)) if match else None


def non_creation_intent(text):
    """Conservative fallback for requests that must never create a quotation."""
    normalized, offsets = normalized_with_map(text.strip())
    if not re.search(r'\bbao gia\b', normalized):
        return None
    if re.search(r'\b(?:dung|khong|chua|cam)\s+(?:\w+\s+){0,3}(?:tao|lap|len|soan)\b', normalized):
        return 'decline', None
    if re.search(r'\b(?:cach|huong dan|lam sao|the nao|quy trinh)\b', normalized):
        return 'help', None
    if re.search(r'\b(?:tim|tra cuu|liet ke|kiem tra|xem)\s+(?:\w+\s+){0,3}bao gia\b', normalized):
        match = re.search(r'\b(?:cua|cho)\s+(.+?)[.!?]*$', normalized)
        return 'lookup', original_slice(text.strip(), offsets, *match.span(1)) if match else None
    return None


def bare_customer_name(text, pending=None):
    """Accept a literal answer to the customer question or a clear proper name."""
    value = text.strip(' \t\r\n,;.!?')
    normalized = plain(value)
    if not value or len(value) > 200 or re.search(r'\d|\bbao gia\b', normalized):
        return None
    if normalized in {'mac dinh', 'benh vien', 'thuong mai', 'co', 'khong'}:
        return None
    if pending == 'customer':
        return value
    words = value.split()
    if len(words) >= 3 and len([word for word in words if word[:1].isupper()]) >= 2:
        return value
    return None


def _quantity(token):
    value = plain(token).strip()
    return int(value) if value.isdigit() else NUMBER_WORDS.get(value)


def _product_lines(clause):
    norm, offsets = normalized_with_map(clause)
    if re.search(r'\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b', norm):
        return []
    amount = r'(?:\d+|' + '|'.join(NUMBER_WORDS) + r')'
    match_trailing = re.fullmatch(
        r'(?:san pham\s*:?\s*)?(.+?)\s*(?:[,;-]\s*)?(?:lấy|lay|số lượng|so luong|sl)\s*:?\s*('
        + amount + r')(?:\s+' + UNITS + r')?(?:\s+nhé|\s+nhe|\s+ạ|\s+a)?',
        norm,
    )
    if match_trailing:
        product = original_slice(clause, offsets, *match_trailing.span(1)).strip(' :,-')
        if (
            product
            and re.search(r'[a-z0-9]', plain(product))
            and plain(product) not in {'khong', 'dung', 'chua'}
            and not re.search(r'\b(?:ngay|han|thanh toan|bang gia|bao gia)\b', plain(product))
        ):
            return [(product, _quantity(match_trailing.group(2)))]
    starts = list(re.finditer(r'\b' + amount + r'\b\s*(?:' + UNITS + r'\b\s*)?', norm))
    result = []
    if starts:
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(norm)
            product = original_slice(clause, offsets, match.end(), end)
            product = re.sub(r'\s+(?:và|va)\s*$', '', product, flags=re.I).strip()
            p_plain = plain(product)
            if (
                product
                and re.search(r'[a-z0-9]', p_plain)
                and p_plain not in {'nhe', 'a', 'di', 'voi'}
                and not re.search(r'\b(?:ngay|han|thanh toan|gia|bao gia)\b', p_plain)
            ):
                result.append((product, _quantity(match.group().split()[0])))
    if result:
        return result
    return []


def extract(text, draft=None, session=None, today=None, strict=False):
    draft, session = draft or {}, session or {}
    original = text.strip()
    norm = plain(original)
    active = draft.get('draft_type') == 'quotation'
    creating = requests_fresh_quote(original)
    if not creating and not active:
        return None
    if re.search(r'\b(?:dung|khong|chua)\s+' + ACTION + r'\b', norm):
        return None
    if active:
        amount = r'(?:\d+|' + '|'.join(NUMBER_WORDS) + r')'
        correction = re.search(r'\b(?:doi|sua)(?:\s+tu\s+\d+)?\s+(?:len|thanh|xuong)\s+(' + amount + r')\b', norm)
        if correction:
            return {'action': 'update', 'quantity': _quantity(correction[1])}
        alternatives = re.findall(r'\b(?:chuyen sang|doi sang|lay lai|lay|con)\s+(' + amount + r')\b', norm)
        if alternatives and not (len(alternatives) == 1 and re.search(r'\bkhong\s+lay\b', norm)):
            return {'action': 'update', 'quantity': _quantity(alternatives[-1])}
    if active and re.fullmatch(r'\d+\s+ngay(?:\s+nhe)?', norm):
        pending = pending_field(session, draft)
        if pending == 'payment_term':
            return {'action': 'update', 'payment_term_query': f'{norm.split()[0]} ngày'}
        if pending == 'validity_date':
            return {'action': 'update', 'validity_date': ((today or date.today()) + timedelta(days=int(norm.split()[0]))).isoformat()}
        return None
    data = draft.get('quotation_data') or {}
    facts = {'action': 'start' if creating else 'update'}
    working_original = original
    from app.business.quote_local import _extract_payment_term_phrase
    pre_term, rest_after_term = _extract_payment_term_phrase(working_original, draft)
    if pre_term:
        facts['payment_term_query'] = pre_term
        working_original = rest_after_term
    clauses = [part.strip() for part in re.split(r'[,;\n]+', working_original) if part.strip()]
    lines = []
    unknown = False
    labelled_customer = explicit_customer_name(original)
    if labelled_customer:
        facts['customer_query'] = labelled_customer
    elif active and '\n' not in original and ',' not in original and ';' not in original:
        name = bare_customer_name(original, pending_field(session, draft))
        if name:
            facts['customer_query'] = name
            return facts
    for clause in clauses:
        normalized, offsets = normalized_with_map(clause)
        # Explicit date and payment terms use distinct anchors; bare DD/MM/YYYY is accepted when active draft needs validity_date.
        date_match = re.search(r'\b(?:het han|hieu luc den|co hieu luc den)(?:\s+bao gia)?(?:\s+ngay)?\s*:?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', normalized)
        if not date_match and active and not data.get('validity_date'):
            date_match = re.fullmatch(r'(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+nhe|\s+a)?', normalized.strip())
        if date_match:
            try:
                facts['validity_date'] = date(int(date_match[3]), int(date_match[2]), int(date_match[1])).isoformat()
            except ValueError:
                pass
            continue
        duration = re.search(r'\b(?:bao gia\s+)?(?:co hieu luc|het han sau)\s+(\d+)\s+ngay\b', normalized)
        if duration:
            facts['validity_date'] = ((today or date.today()) + timedelta(days=int(duration[1]))).isoformat()
            continue
        immediate_term = re.search(
            r'\b(?:(?:dieu khoan\s+)?thanh toan|tra(?:\s+tien)?)\s+(ngay(?:\s+lap\s+tuc)?)\b|\b(immediate\s+payment)\b',
            normalized,
        )
        if immediate_term:
            facts['payment_term_query'] = 'Thanh toán ngay'
            continue
        term = re.search(r'\b(?:dieu khoan\s+)?thanh toan(?:\s+trong|\s+la|\s*:)?\s*(\d+\s+ngay)\b|\btra(?:\s+tien)?\s+trong\s+(\d+\s+ngay)\b', normalized)
        if term:
            selected = term.group(1) or term.group(2)
            facts['payment_term_query'] = re.sub(r'ngay$', 'ngày', selected)
            continue
        price = re.search(r'\b(?:bang gia|dung gia|lay gia|gia)\s+(mac dinh|benh vien|thuong mai)\b', normalized)
        if price:
            facts['pricelist_query'] = original_slice(clause, offsets, *price.span(1))
            continue
        if labelled_customer and re.search(r'\b(?:ten khach hang|khach hang|khach)\s*:', normalized):
            continue
        customer = re.search(r'\b(?:bao gia\s+cho|khach hang|khach)\s+(.+)', normalized)
        if not customer:
            customer = re.search(r'^cho\s+(.+?)\s+' + ACTION + r'\s+bao gia\b', normalized)
        if customer:
            name = original_slice(clause, offsets, *customer.span(1))
            name_norm = plain(name)
            name_norm = re.sub(r'\s+(?:nhe|a|voi)$', '', name_norm)
            if name and not re.search(r'\b(?:bao gia|may|san pham)\b', name_norm):
                facts['customer_query'] = name.rstrip(' .,!')
                continue
        product_clause = re.sub(r'^\s*(?:' + ACTION + r'\s+)?(?:' + POLITE + r'\s+)*bao gia\s*', '', normalized)
        if product_clause != normalized:
            start = len(normalized) - len(product_clause)
            clause = original_slice(clause, offsets, start, len(normalized))
        parsed_lines = _product_lines(clause)
        lines.extend(parsed_lines)
        if not parsed_lines and not re.fullmatch(r'(?:' + ACTION + r'|' + POLITE + r'|bao|gia|\s)+', plain(clause)):
            unknown = True
    if lines:
        facts['line_updates'] = [{'line_index': i, 'product_query': name, 'quantity': amount}
                                 for i, (name, amount) in enumerate(lines, 1)]
    if active and not lines:
        if not facts.get('pricelist_query') and re.fullmatch(r'(?:dung\s+)?(?:gia\s+)?(?:mac dinh|benh vien|thuong mai)(?:\s+nhe|\s+a|\s+voi)?', norm):
            normalized, offsets = normalized_with_map(original)
            choice = re.search(r'mac dinh|benh vien|thuong mai', normalized)
            facts['pricelist_query'] = original_slice(original, offsets, *choice.span())
            unknown = False
    if strict and unknown:
        return None
    return facts if len(facts) > 1 or creating else None
