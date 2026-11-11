import time

from app.services.conversation_history_store import ConversationHistoryStore


def test_chat_expiry_keeps_business_draft_but_drops_history_and_candidates(tmp_path):
    store = ConversationHistoryStore(str(tmp_path / 'context.db'), ttl_seconds=1)
    draft = {'draft_type': 'quotation', 'draft_id': 'quote_demo', 'quotation_data': {'lines': [{'quantity': 2}]}}
    saved = store.set_active_draft('tenant', 1, 7, 'binding', draft, database='db')
    store.append_turn('tenant', 1, 7, 'binding', 'user', 'old text', database='db')
    store.set_last_read_data('tenant', 1, 7, 'binding', 'business_candidates_products', {'items': [{'id': 3}]}, database='db')
    with store._get_connection() as conn:
        conn.execute('UPDATE conversation_sessions SET updated_at=?', (time.time() - 7200,))
    restored = store.get_session('tenant', 1, 7, 'binding', database='db')
    assert restored['active_draft']['draft_id'] == saved['draft_id']
    assert restored['active_draft']['quotation_data']['lines'][0]['quantity'] == 2
    assert restored['history'] == [] and restored['last_read'] == {}
    assert not store.get_session('tenant', 1, 7, 'different-binding', database='db')['active_draft']


def test_expired_email_context_is_not_retained(tmp_path):
    store = ConversationHistoryStore(str(tmp_path / 'context.db'), ttl_seconds=1)
    store.set_active_draft('tenant', 1, 7, 'binding', {'draft_type': 'email', 'content': 'old email'}, database='db')
    with store._get_connection() as conn:
        conn.execute('UPDATE conversation_sessions SET updated_at=?', (time.time() - 7200,))
    assert store.get_session('tenant', 1, 7, 'binding', database='db')['active_draft'] is None
