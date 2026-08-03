"""
Comprehensive test suite for the enhanced TaskManager with conversation awareness.

Tests all three cases from the requirements:
- Case 1: Original conversation still active
- Case 2: No conversation active (wake-word mode)
- Case 3: Different conversation active

Run with: python -m pytest App/core/tests/test_task_manager.py -v
"""

import unittest
import threading
import time
import sys
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Add the App directory to the path so we can import core modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import core.TaskManager as tm
from core.TaskManager import (
    BackgroundTask,
    TaskStatus,
    TaskPriority,
    DeliveryMethod,
    NotificationPreference,
    NotificationStrategy,
    TaskRegistry,
    CompletionQueue,
    ConversationStateManager,
    NotificationDecisionEngine,
    SubAgent,
    ResearchAgent,
    TimerAgent,
    dispatch_background_task,
    deliver_notification,
    get_registry,
    get_completion_queue,
    update_conversation_state,
    set_speaking_state,
    set_listening_state,
    set_mode,
    user_activity_occurred,
)

# Never write test tasks into App/data/tasks/tasks.json
_ORIG_TASK_STORE = tm.TASK_STORE_PATH
_TEST_TASK_STORE = Path(tempfile.gettempdir()) / f"ava_tm_suite_{os.getpid()}.json"


def setUpModule():
    tm.TASK_STORE_PATH = _TEST_TASK_STORE
    TaskRegistry._instance = None
    try:
        _TEST_TASK_STORE.unlink()
    except OSError:
        pass


def tearDownModule():
    tm.TASK_STORE_PATH = _ORIG_TASK_STORE
    TaskRegistry._instance = None
    try:
        _TEST_TASK_STORE.unlink()
    except OSError:
        pass




class TestBackgroundTask(unittest.TestCase):
    """Tests for the BackgroundTask dataclass"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.task = BackgroundTask(
            id="test123",
            name="Test Research",
            description="Testing research task",
            conversation_id="conv_001",
            conversation_name="Test Conversation",
            original_query="Research AI safety",
            task_type="research",
            priority=TaskPriority.NORMAL
        )
    
    def test_task_creation(self):
        """Test basic task creation"""
        self.assertEqual(self.task.id, "test123")
        self.assertEqual(self.task.name, "Test Research")
        self.assertEqual(self.task.conversation_id, "conv_001")
        self.assertEqual(self.task.conversation_name, "Test Conversation")
        self.assertEqual(self.task.original_query, "Research AI safety")
        self.assertEqual(self.task.task_type, "research")
        self.assertEqual(self.task.priority, TaskPriority.NORMAL)
        self.assertEqual(self.task.status, TaskStatus.PENDING)
    
    def test_task_completion(self):
        """Test task completion and timestamps"""
        self.task.status = TaskStatus.COMPLETED
        self.task.result = "Research results here"
        self.task.completed_at = self.task.created_at  # Simulate instant completion
        
        summary = self.task.to_summary()
        self.assertIn("Test Research", summary)
        self.assertIn("Test Conversation", summary)
        self.assertIn("Research results here", summary)
    
    def test_task_to_conversation_context(self):
        """Test conversation context formatting"""
        context = self.task.to_conversation_context()
        self.assertIn("Task ID: test123", context)
        self.assertIn("Type: research", context)
        self.assertIn("Test Conversation", context)
        self.assertIn("Research AI safety", context)
    
    def test_task_to_idle_notification(self):
        """Test idle notification formatting"""
        self.task.status = TaskStatus.COMPLETED
        self.task.result = "Done!"
        
        notification = self.task.to_idle_notification()
        self.assertIn("Test Research", notification)
        self.assertIn("Test Conversation", notification)
        self.assertIn("has finished", notification)
    
    def test_task_to_different_conversation_notification(self):
        """Test different conversation notification formatting"""
        self.task.status = TaskStatus.COMPLETED
        self.task.result = "Done!"
        
        notification = self.task.to_different_conversation_notification()
        self.assertIn("Test Research", notification)
        self.assertIn("has finished", notification)
    
    def test_task_to_dict(self):
        """Test task serialization"""
        task_dict = self.task.to_dict()
        self.assertEqual(task_dict["id"], "test123")
        self.assertEqual(task_dict["name"], "Test Research")
        self.assertEqual(task_dict["conversation_id"], "conv_001")
        self.assertEqual(task_dict["priority"], "normal")
    
    def test_task_from_dict(self):
        """Test task deserialization"""
        task_dict = {
            "id": "test456",
            "name": "Another Task",
            "description": "Another description",
            "status": "completed",
            "conversation_id": "conv_002",
            "conversation_name": "Another Conversation",
            "original_query": "Do something",
            "task_type": "timer",
            "priority": "high",
            "created_at": "2026-01-01T12:00:00",
        }
        
        task = BackgroundTask.from_dict(task_dict)
        self.assertEqual(task.id, "test456")
        self.assertEqual(task.name, "Another Task")
        self.assertEqual(task.conversation_id, "conv_002")
        self.assertEqual(task.priority, TaskPriority.HIGH)
        self.assertEqual(task.status, TaskStatus.COMPLETED)


class TestTaskRegistry(unittest.TestCase):
    """Tests for the TaskRegistry singleton"""
    
    def setUp(self):
        """Set up test fixtures - clear registry before each test"""
        registry = TaskRegistry()
        registry.clear_all()
    
    def test_singleton(self):
        """Test that TaskRegistry is a singleton"""
        registry1 = TaskRegistry()
        registry2 = TaskRegistry()
        self.assertIs(registry1, registry2)
    
    def test_create_task(self):
        """Test task creation"""
        registry = TaskRegistry()
        task = registry.create_task(
            name="Test Task",
            description="A test task",
            conversation_id="conv_001",
            conversation_name="Test Conv",
            task_type="research"
        )
        
        self.assertEqual(task.name, "Test Task")
        self.assertEqual(task.conversation_id, "conv_001")
        self.assertEqual(task.conversation_name, "Test Conv")
        self.assertEqual(task.task_type, "research")
        
        # Check it was registered
        retrieved = registry.get_task(task.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Test Task")

    def test_task_store_persists_and_reloads(self):
        """Test JSON task store persistence and reload"""
        registry = TaskRegistry()
        task = registry.create_task(
            name="Persistent Task",
            description="Stored on disk",
            conversation_id="conv_disk",
            task_type="delegate",
        )
        registry.update_task(task.id, TaskStatus.COMPLETED, result="Stored result")

        self.assertTrue(tm.TASK_STORE_PATH.exists())

        TaskRegistry._instance = None
        reloaded = TaskRegistry()
        loaded_task = reloaded.get_task(task.id)

        self.assertIsNotNone(loaded_task)
        self.assertEqual(loaded_task.name, "Persistent Task")
        self.assertEqual(loaded_task.result, "Stored result")
        reloaded.clear_all()
    
    def test_get_tasks_for_conversation(self):
        """Test getting tasks by conversation ID"""
        registry = TaskRegistry()
        
        # Create tasks for different conversations
        task1 = registry.create_task("Task 1", "Desc 1", conversation_id="conv_A")
        task2 = registry.create_task("Task 2", "Desc 2", conversation_id="conv_A")
        task3 = registry.create_task("Task 3", "Desc 3", conversation_id="conv_B")
        
        tasks_a = registry.get_tasks_for_conversation("conv_A")
        self.assertEqual(len(tasks_a), 2)
        task_ids = {t.id for t in tasks_a}
        self.assertIn(task1.id, task_ids)
        self.assertIn(task2.id, task_ids)
        
        tasks_b = registry.get_tasks_for_conversation("conv_B")
        self.assertEqual(len(tasks_b), 1)
        self.assertEqual(tasks_b[0].id, task3.id)
    
    def test_get_running_tasks(self):
        """Test filtering running tasks"""
        registry = TaskRegistry()
        
        task1 = registry.create_task("Running Task", "", conversation_id="conv_1")
        registry.update_task(task1.id, TaskStatus.RUNNING)
        
        task2 = registry.create_task("Pending Task", "", conversation_id="conv_1")
        
        task3 = registry.create_task("Completed Task", "", conversation_id="conv_1")
        registry.update_task(task3.id, TaskStatus.COMPLETED)
        
        running = registry.get_running_tasks()
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0].id, task1.id)
    
    def test_mark_as_notified(self):
        """Test marking tasks as notified"""
        registry = TaskRegistry()
        task = registry.create_task("Test", "", conversation_id="conv_1")
        registry.update_task(task.id, TaskStatus.COMPLETED)

        # Before marking
        pending = registry.get_pending_notifications()
        self.assertEqual(len(pending), 1)

        # Mark as notified — keep COMPLETED so manage result still works
        registry.mark_as_notified(task.id)

        # After marking
        task = registry.get_task(task.id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(task.notified_at)
        pending_after = registry.get_pending_notifications()
        self.assertEqual(len(pending_after), 0)
    
    def test_cleanup_old_tasks(self):
        """Test cleanup of old completed tasks"""
        registry = TaskRegistry()
        
        # Create old task with old completion time
        old_task = registry.create_task("Old Task", "", conversation_id="conv_1")
        old_completed_at = datetime(2020, 1, 1, 12, 0, 0)
        with registry._tasks_lock:
            if old_task.id in registry._tasks:
                task = registry._tasks[old_task.id]
                task.status = TaskStatus.COMPLETED
                task.completed_at = old_completed_at
        
        # Create recent task
        recent_task = registry.create_task("Recent Task", "", conversation_id="conv_1")
        registry.update_task(recent_task.id, TaskStatus.COMPLETED)
        
        # Cleanup tasks older than 24 hours
        registry.cleanup_old_tasks(max_age_hours=24)
        
        # Old task should be removed
        self.assertIsNone(registry.get_task(old_task.id))
        # Recent task should remain
        self.assertIsNotNone(registry.get_task(recent_task.id))


class TestConversationStateManager(unittest.TestCase):
    """Tests for the ConversationStateManager singleton"""
    
    def setUp(self):
        """Reset state manager before each test"""
        state_manager = ConversationStateManager()
        state_manager.reset()
    
    def test_singleton(self):
        """Test that ConversationStateManager is a singleton"""
        mgr1 = ConversationStateManager()
        mgr2 = ConversationStateManager()
        self.assertIs(mgr1, mgr2)
    
    def test_conversation_lifecycle(self):
        """Test conversation start/end lifecycle"""
        state_manager = ConversationStateManager()
        
        # Initially no conversation
        self.assertIsNone(state_manager.current_conversation_id)
        self.assertFalse(state_manager.is_conversation_active)
        
        # Start conversation
        state_manager.conversation_started("conv_001", "Test Conversation")
        self.assertEqual(state_manager.current_conversation_id, "conv_001")
        self.assertEqual(state_manager.current_conversation_name, "Test Conversation")
        self.assertTrue(state_manager.is_conversation_active)
        
        # End conversation
        state_manager.conversation_ended()
        self.assertFalse(state_manager.is_conversation_active)
        # Conversation ID should persist until next one starts
        self.assertEqual(state_manager.current_conversation_id, "conv_001")
    
    def test_user_activity(self):
        """Test user activity tracking"""
        state_manager = ConversationStateManager()
        
        # Start conversation
        state_manager.conversation_started("conv_001", "Test")
        
        # Check idle duration initially
        idle_duration = state_manager.get_idle_duration()
        self.assertLess(idle_duration.total_seconds(), 1)
        
        # User spoke
        time.sleep(0.1)
        state_manager.user_spoke()
        new_idle = state_manager.get_idle_duration()
        self.assertLess(new_idle.total_seconds(), 0.2)
    
    def test_speaking_listening_states(self):
        """Test speaking and listening state tracking"""
        state_manager = ConversationStateManager()
        
        # Initially not speaking or listening
        self.assertFalse(state_manager.is_speaking)
        self.assertFalse(state_manager.is_listening)
        
        # Set speaking
        state_manager.is_speaking = True
        self.assertTrue(state_manager.is_speaking)
        
        # Set listening
        state_manager.is_listening = True
        self.assertTrue(state_manager.is_listening)
        
        # Reset
        state_manager.is_speaking = False
        state_manager.is_listening = False
        self.assertFalse(state_manager.is_speaking)
        self.assertFalse(state_manager.is_listening)
    
    def test_mode_tracking(self):
        """Test operational mode tracking"""
        state_manager = ConversationStateManager()
        
        state_manager.current_mode = "wakeword"
        self.assertEqual(state_manager.current_mode, "wakeword")
        
        state_manager.current_mode = "continuous"
        self.assertEqual(state_manager.current_mode, "continuous")
        
        state_manager.current_mode = "tui"
        self.assertEqual(state_manager.current_mode, "tui")


class TestNotificationDecisionEngine(unittest.TestCase):
    """Tests for the NotificationDecisionEngine - covers all three cases"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Clear and reset state
        state_manager = ConversationStateManager()
        state_manager.reset()
        
        registry = TaskRegistry()
        registry.clear_all()
        
        completion_queue = CompletionQueue()
        # Clear the queue by popping all
        while completion_queue.pop(timeout=0.01):
            pass
    
    def _create_completed_task(self, conversation_id="conv_001", conversation_name="Conv 1",
                               original_query="Test query", priority=TaskPriority.NORMAL) -> BackgroundTask:
        """Helper to create a completed task"""
        registry = TaskRegistry()
        task = registry.create_task(
            name="Test Research",
            description="Testing",
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=original_query,
            task_type="research",
            priority=priority
        )
        registry.update_task(task.id, TaskStatus.COMPLETED, result="Research results")
        return task
    
    def test_case_1_original_conversation_active(self):
        """
        Case 1: Original conversation still active
        Task should be delivered via NEXT_RESPONSE
        """
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_001", "Test Conversation")
        state_manager.is_conversation_active = True
        
        task = self._create_completed_task("conv_001", "Test Conversation")
        
        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)
        
        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)
        self.assertFalse(strategy.interrupt_current)
        self.assertEqual(strategy.task_id, task.id)
        # Compact DONE payload (token-cheap), not full conversation context
        self.assertIn(task.id, strategy.notification_text)
        self.assertIn("Finished:", strategy.notification_text)
        # Held origin/dossier block for later "run/open" continuity
        self.assertIn("ORIGIN", strategy.notification_text)
    
    def test_case_2_no_conversation_active_wakeword_mode(self):
        """
        Case 2: No conversation active (wake-word mode)
        Low priority tasks should QUEUE_FOR_IDLE
        High priority tasks should IMMEDIATE
        """
        state_manager = ConversationStateManager()
        state_manager.current_mode = "wakeword"
        state_manager.is_conversation_active = False
        
        # Low priority task
        task_low = self._create_completed_task("conv_001", "Previous Conv", priority=TaskPriority.LOW)
        engine = NotificationDecisionEngine()
        strategy_low = engine.decide_notification_strategy(task_low)
        
        self.assertEqual(strategy_low.delivery_method, DeliveryMethod.QUEUE_FOR_IDLE)
        self.assertFalse(strategy_low.interrupt_current)
        
        # High priority task
        task_high = self._create_completed_task("conv_001", "Previous Conv", priority=TaskPriority.HIGH)
        strategy_high = engine.decide_notification_strategy(task_high)
        
        self.assertEqual(strategy_high.delivery_method, DeliveryMethod.IMMEDIATE)
        self.assertTrue(strategy_high.interrupt_current)
    
    def test_case_2_idle_mode(self):
        """
        Case 2: Idle mode (no conversation, not wake-word)
        """
        state_manager = ConversationStateManager()
        state_manager.current_mode = "idle"
        state_manager.is_conversation_active = False
        
        task = self._create_completed_task("conv_001", "Previous Conv")
        
        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)
        
        self.assertEqual(strategy.delivery_method, DeliveryMethod.QUEUE_FOR_IDLE)
    
    def test_case_3_different_conversation_active_still_speaks(self):
        """
        Case 3: Different conversation active — still NEXT_RESPONSE.

        User is present; main must announce. Sticky inbox is for offline only.
        """
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_002", "Current Conversation")
        state_manager.is_conversation_active = True
        state_manager.current_mode = "continuous"

        task = self._create_completed_task(
            conversation_id="conv_001",
            conversation_name="Previous Conversation",
            original_query="Research AI safety machine learning"
        )

        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)

        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)
        self.assertIn(task.id, strategy.notification_text)

    def test_empty_conversation_id_while_chatting_is_next_response(self):
        """Live bug: demo finished with conversation_id='' → silent DEFER."""
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_live", "Live Chat")
        state_manager.is_conversation_active = True
        state_manager.current_mode = "tui"

        task = self._create_completed_task(
            conversation_id="",
            conversation_name="",
            original_query="Demo task will run for 50 seconds",
        )
        task.task_type = "demo"
        task.kind = "long"

        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)
        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)
    
    def test_case_3_urgent_priority_while_chatting(self):
        """Urgent + active chat → NEXT_RESPONSE (main speaks; no silent drop)."""
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_002", "Current Conversation")
        state_manager.is_conversation_active = True
        state_manager.current_mode = "continuous"

        task = self._create_completed_task(
            conversation_id="conv_001",
            conversation_name="Previous Conversation",
            priority=TaskPriority.URGENT
        )

        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)

        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)
        self.assertEqual(strategy.priority, TaskPriority.URGENT)

    def test_urgent_priority_immediate_when_away(self):
        """Urgent/timer while no active chat → IMMEDIATE hard alert."""
        state_manager = ConversationStateManager()
        state_manager.is_conversation_active = False
        state_manager.current_mode = "wakeword"

        task = self._create_completed_task(
            conversation_id="conv_001",
            priority=TaskPriority.URGENT
        )

        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)

        self.assertEqual(strategy.delivery_method, DeliveryMethod.IMMEDIATE)
        self.assertTrue(strategy.interrupt_current)


class TestCompletionQueue(unittest.TestCase):
    """Tests for the CompletionQueue"""
    
    def setUp(self):
        """Clear queue before each test"""
        queue = CompletionQueue()
        # Clear by popping all
        while queue.pop(timeout=0.01):
            pass
        queue._idle_notifications.clear()
        queue._deferred_notifications.clear()
        queue._pending_by_conversation.clear()
    
    def test_singleton(self):
        """Test that CompletionQueue is a singleton"""
        queue1 = CompletionQueue()
        queue2 = CompletionQueue()
        self.assertIs(queue1, queue2)
    
    def test_push_and_pop(self):
        """Test basic push/pop operations"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        registry.clear_all()
        
        task = registry.create_task("Test", "", conversation_id="conv_1")
        registry.update_task(task.id, TaskStatus.COMPLETED, result="Done")
        
        queue.push(task)
        
        # Small delay for priority queue
        time.sleep(0.01)
        
        popped = queue.pop(timeout=0.1)
        self.assertIsNotNone(popped)
        self.assertEqual(popped.id, task.id)
    
    def test_priority_ordering(self):
        """Test that high priority tasks are delivered first"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        registry.clear_all()
        
        # Create tasks with different priorities
        task_low = registry.create_task("Low", "", conversation_id="conv_1")
        registry.update_task(task_low.id, TaskStatus.COMPLETED)
        task_low.priority = TaskPriority.LOW
        
        task_high = registry.create_task("High", "", conversation_id="conv_1")
        registry.update_task(task_high.id, TaskStatus.COMPLETED)
        task_high.priority = TaskPriority.HIGH
        
        task_urgent = registry.create_task("Urgent", "", conversation_id="conv_1")
        registry.update_task(task_urgent.id, TaskStatus.COMPLETED)
        task_urgent.priority = TaskPriority.URGENT
        
        # Push all tasks
        queue.push(task_low)
        queue.push(task_high)
        queue.push(task_urgent)
        
        # Small delay
        time.sleep(0.01)
        
        # Pop should return urgent first
        first = queue.pop(timeout=0.1)
        self.assertEqual(first.id, task_urgent.id)
        
        second = queue.pop(timeout=0.1)
        self.assertEqual(second.id, task_high.id)
        
        third = queue.pop(timeout=0.1)
        self.assertEqual(third.id, task_low.id)
    
    def test_conversation_specific_notifications(self):
        """Test storing notifications by conversation"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        registry.clear_all()
        
        task1 = registry.create_task("Task 1", "", conversation_id="conv_A")
        registry.update_task(task1.id, TaskStatus.COMPLETED)
        
        task2 = registry.create_task("Task 2", "", conversation_id="conv_B")
        registry.update_task(task2.id, TaskStatus.COMPLETED)
        
        queue.store_for_conversation(task1)
        queue.store_for_conversation(task2)
        
        # Get notifications for conv_A
        conv_a_notifications = queue.get_notifications_for_conversation("conv_A")
        self.assertEqual(len(conv_a_notifications), 1)
        self.assertEqual(conv_a_notifications[0].id, task1.id)
        
        # Get notifications for conv_B
        conv_b_notifications = queue.get_notifications_for_conversation("conv_B")
        self.assertEqual(len(conv_b_notifications), 1)
        self.assertEqual(conv_b_notifications[0].id, task2.id)
    
    def test_idle_notifications(self):
        """Test idle notification storage"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        registry.clear_all()
        
        task = registry.create_task("Idle Task", "", conversation_id="conv_1")
        registry.update_task(task.id, TaskStatus.COMPLETED)
        
        queue.store_for_idle(task)
        
        idle_tasks = queue.get_idle_notifications()
        self.assertEqual(len(idle_tasks), 1)
        self.assertEqual(idle_tasks[0].id, task.id)
    
    def test_has_pending(self):
        """Test has_pending method"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        registry.clear_all()
        
        self.assertFalse(queue.has_pending())
        
        task = registry.create_task("Test", "", conversation_id="conv_1")
        registry.update_task(task.id, TaskStatus.COMPLETED)
        queue.push(task)
        
        self.assertTrue(queue.has_pending())
    
    def test_backward_compatibility_store_pending(self):
        """Test backward compatibility with store_pending"""
        queue = CompletionQueue()
        
        queue.store_pending("Test summary")
        
        # Should be stored in idle notifications
        self.assertTrue(queue.has_pending())
        
        items = queue.get_and_clear_pending()
        self.assertEqual(len(items), 1)
        self.assertIn("Test summary", items[0])


class TestSubAgent(unittest.TestCase):
    """Tests for SubAgent base class and concrete implementations"""
    
    def setUp(self):
        """Clean up before each test"""
        registry = TaskRegistry()
        registry.clear_all()
        
        state_manager = ConversationStateManager()
        state_manager.reset()
    
    def test_timer_agent(self):
        """Test TimerAgent execution"""
        task_id = TimerAgent(
            duration_seconds=0.1,
            message="Test timer",
            conversation_id="conv_001",
            conversation_name="Test Conv"
        ).start()
        
        self.assertIsNotNone(task_id)
        self.assertEqual(len(task_id), 8)  # UUID prefix
        
        # Wait for completion
        time.sleep(0.2)
        
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.result, "Test timer")
    
    def test_research_agent_creation(self):
        """Test ResearchAgent creation (don't execute as it calls external API)"""
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_001", "Test Conv")
        
        agent = ResearchAgent(
            topic="Test topic",
            # conversation_id and name should be auto-filled from state
        )
        
        self.assertEqual(agent.task.conversation_id, "conv_001")
        self.assertEqual(agent.task.conversation_name, "Test Conv")
        self.assertEqual(agent.task.original_query, "Test topic")
        self.assertEqual(agent.task.task_type, "research")


class TestDispatchBackgroundTask(unittest.TestCase):
    """Tests for the dispatch_background_task function"""
    
    def setUp(self):
        """Clean up before each test"""
        registry = TaskRegistry()
        registry.clear_all()
        
        state_manager = ConversationStateManager()
        state_manager.reset()
    
    def test_dispatch_research_task(self):
        """Test dispatching a research task"""
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_001", "Test Conv")
        
        task_id, message = dispatch_background_task(
            "research",
            topic="AI Safety"
        )
        
        self.assertIsNotNone(task_id)
        self.assertIn("started", message.lower())
        
        # Verify task was created with conversation context
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.conversation_id, "conv_001")
        self.assertEqual(task.conversation_name, "Test Conv")
        self.assertEqual(task.task_type, "research")
    
    def test_dispatch_timer_task(self):
        """Test dispatching a timer task"""
        task_id, message = dispatch_background_task(
            "timer",
            duration=5,
            message="5 second timer"
        )
        
        self.assertIsNotNone(task_id)
        
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, "timer")
    
    def test_unknown_task_type(self):
        """Test handling of unknown task types"""
        task_id, message = dispatch_background_task("unknown_task")
        
        self.assertEqual(task_id, "")
        self.assertIn("Unknown", message)

    def test_dispatch_delegate_task(self):
        """Test dispatching a general delegated AI task"""
        task_id, message = dispatch_background_task(
            "delegate",
            objective="Summarize a local note",
            context="No note supplied",
            expected_output="A short status",
        )

        self.assertIsNotNone(task_id)
        self.assertIn("started", message.lower())

        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, "delegate")
        self.assertEqual(task.original_query, "Summarize a local note")

    def test_dispatch_mission_task(self):
        """Test dispatching a supervisor mission task"""
        task_id, message = dispatch_background_task(
            "mission",
            mission="Compare three options",
            max_workers=2,
        )

        self.assertIsNotNone(task_id)
        self.assertIn("started", message.lower())

        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, "mission")


class TestIntegrationScenarios(unittest.TestCase):
    """Integration tests for complete workflow scenarios"""
    
    def setUp(self):
        """Clean up all state before each test"""
        registry = TaskRegistry()
        registry.clear_all()
        
        queue = CompletionQueue()
        while queue.pop(timeout=0.01):
            pass
        
        state_manager = ConversationStateManager()
        state_manager.reset()
    
    def test_case_1_complete_workflow(self):
        """
        Case 1 Complete Workflow:
        1. Start conversation A
        2. Dispatch background task
        3. Conversation A continues (still active)
        4. Task completes
        5. Verify notification strategy is NEXT_RESPONSE
        """
        # Step 1: Start conversation A
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_A", "Conversation A")
        state_manager.is_conversation_active = True
        
        # Step 2: Dispatch background task
        task_id, _ = dispatch_background_task(
            "research",
            topic="AI Safety"
        )
        
        # Verify task has correct conversation context
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        self.assertEqual(task.conversation_id, "conv_A")
        
        # Step 3: Simulate task completion
        registry.update_task(task_id, TaskStatus.COMPLETED, result="Research done")
        
        # Step 4: Process completion
        queue = CompletionQueue()
        queue.push(task)
        
        # Step 5: Verify decision
        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)
        
        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)
        self.assertFalse(strategy.interrupt_current)
    
    def test_case_2_complete_workflow(self):
        """
        Case 2 Complete Workflow:
        1. Start conversation A
        2. Dispatch background task
        3. Conversation A ends (user stops talking)
        4. System goes to wake-word mode
        5. Task completes
        6. Verify notification is queued for idle
        """
        # Step 1: Start conversation A
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_A", "Conversation A")
        state_manager.is_conversation_active = True
        
        # Step 2: Dispatch background task
        task_id, _ = dispatch_background_task(
            "research",
            topic="Quantum Computing"
        )
        
        # Step 3: Conversation ends
        state_manager.is_conversation_active = False
        state_manager.current_mode = "wakeword"
        
        # Step 4: Simulate task completion
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        registry.update_task(task_id, TaskStatus.COMPLETED, result="Done")
        
        # Step 5: Process completion
        queue = CompletionQueue()
        queue.push(task)
        
        # Step 6: Verify decision
        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)
        
        self.assertEqual(strategy.delivery_method, DeliveryMethod.QUEUE_FOR_IDLE)
    
    def test_case_3_complete_workflow(self):
        """
        Case 3 Complete Workflow:
        1. Start conversation A
        2. Dispatch background task
        3. Conversation A ends
        4. Start conversation B
        5. Task completes
        6. User is still chatting in B → NEXT_RESPONSE (main speaks)
        """
        # Step 1: Start conversation A
        state_manager = ConversationStateManager()
        state_manager.conversation_started("conv_A", "Conversation A")
        state_manager.is_conversation_active = True
        state_manager.current_mode = "continuous"

        # Step 2: Dispatch background task
        task_id, _ = dispatch_background_task(
            "research",
            topic="Climate Change"
        )

        # Step 3: Conversation A ends
        state_manager.is_conversation_active = False

        # Step 4: Start conversation B
        state_manager.conversation_started("conv_B", "Conversation B")
        state_manager.is_conversation_active = True
        state_manager.current_mode = "continuous"

        # Step 5: Simulate task completion
        registry = TaskRegistry()
        task = registry.get_task(task_id)
        registry.update_task(task_id, TaskStatus.COMPLETED, result="Done")

        # Step 6: Verify decision — present user gets a spoken update
        engine = NotificationDecisionEngine()
        strategy = engine.decide_notification_strategy(task)

        self.assertEqual(strategy.delivery_method, DeliveryMethod.NEXT_RESPONSE)


class TestConcurrency(unittest.TestCase):
    """Tests for thread safety and concurrent operations"""
    
    def setUp(self):
        """Clean up before each test"""
        registry = TaskRegistry()
        registry.clear_all()
        
        queue = CompletionQueue()
        while queue.pop(timeout=0.01):
            pass
    
    def test_concurrent_task_creation(self):
        """Test thread-safe task creation"""
        registry = TaskRegistry()
        tasks = []
        errors = []
        
        def create_task(i):
            try:
                task = registry.create_task(
                    f"Task {i}",
                    f"Description {i}",
                    conversation_id=f"conv_{i % 3}"
                )
                tasks.append(task)
            except Exception as e:
                errors.append(e)
        
        # Create tasks concurrently
        threads = [threading.Thread(target=create_task, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(tasks), 50)
        
        # Verify all tasks are in registry
        all_tasks = registry.get_all_tasks()
        self.assertEqual(len(all_tasks), 50)
    
    def test_concurrent_queue_operations(self):
        """Test thread-safe queue operations"""
        queue = CompletionQueue()
        registry = TaskRegistry()
        errors = []
        
        def push_task(i):
            try:
                task = registry.create_task(f"Task {i}", "", conversation_id="conv_1")
                registry.update_task(task.id, TaskStatus.COMPLETED)
                queue.push(task)
            except Exception as e:
                errors.append(e)
        
        # Push tasks concurrently
        threads = [threading.Thread(target=push_task, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(len(errors), 0)
        
        # Verify we can pop all tasks
        popped_tasks = []
        while True:
            task = queue.pop(timeout=0.1)
            if task is None:
                break
            popped_tasks.append(task)
        
        self.assertEqual(len(popped_tasks), 30)


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)
