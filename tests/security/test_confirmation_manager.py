import pytest
import time
from app.agent.confirmation_manager import confirmation_manager, ConfirmationState

def test_confirmation_manager_lifecycle():
    # 1. Create request
    record = confirmation_manager.create_request("delete_file", {"filepath": "important.txt"}, {"file": "important.txt"}, {"user_id": "test", "chat_id": 123})
    assert record.status == ConfirmationState.PENDING
    
    # 2. Reject
    assert confirmation_manager.reject_confirmation(record.id, user_id="test", chat_id=123) == True
    assert confirmation_manager.get_record(record.id).status == ConfirmationState.REJECTED
    
    # 3. Cannot approve rejected
    assert confirmation_manager.approve_confirmation(record.id, user_id="test", chat_id=123) == False
    
    # 4. New request -> Approve -> Check replay protection
    record2 = confirmation_manager.create_request("delete_file", {"filepath": "important.txt"}, {"file": "important.txt"}, {"user_id": "test", "chat_id": 123})
    assert confirmation_manager.approve_confirmation(record2.id, user_id="test", chat_id=123) == True
    
    # 5. Check and consume (should succeed)
    is_approved = confirmation_manager.check_and_consume_approval("delete_file", {"filepath": "important.txt"}, confirmation_id=record2.id, user_id="test", chat_id=123)
    assert is_approved == True
    
    # 6. Check and consume again (should fail because it's executed)
    is_approved_again = confirmation_manager.check_and_consume_approval("delete_file", {"filepath": "important.txt"}, confirmation_id=record2.id, user_id="test", chat_id=123)
    assert is_approved_again == False
    confirmation_manager.mark_executed(record2.id)
    assert confirmation_manager.get_record(record2.id).status == ConfirmationState.EXECUTED
