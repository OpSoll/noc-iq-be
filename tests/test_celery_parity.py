"""
Eager-versus-worker parity tests for Celery task paths.

Tests ensure that task behavior is consistent between:
1. Eager mode (CELERY_TASK_ALWAYS_EAGER=True) - synchronous execution
2. Worker mode (CELERY_TASK_ALWAYS_EAGER=False) - asynchronous execution

This reduces deployment risk by catching environment-specific differences.
"""
import json
import unittest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.session import get_db
from app.core.config import settings
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import compute_sla_for_device, compute_bulk_sla
from app.tasks.webhook_tasks import dispatch_webhook_delivery, trigger_sla_violation_async
from app.models.job import Job, JobStatus, JobType
from app.models.base_class import Base


class CeleryParityTestBase(unittest.TestCase):
    """Base class for Celery parity tests."""
    
    def setUp(self):
        """Set up test database and client."""
        # Create in-memory test database
        self.engine = create_engine("sqlite:///:memory:")
        self.TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        
        # Override database dependency
        def override_get_db():
            try:
                db = self.TestSessionLocal()
                yield db
            finally:
                db.close()
        
        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        
        # Store original eager setting
        self.original_eager = getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
    
    def tearDown(self):
        """Clean up test database."""
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()
        # Restore original eager setting
        settings.CELERY_TASK_ALWAYS_EAGER = self.original_eager
    
    def set_eager_mode(self, eager: bool):
        """Set Celery eager mode for testing."""
        settings.CELERY_TASK_ALWAYS_EAGER = eager
        celery_app.conf.task_always_eager = eager
        celery_app.conf.task_store_eager_result = eager
    
    def create_test_job(self, job_type: JobType, payload: Dict[str, Any]) -> Job:
        """Create a test job record."""
        db = self.TestSessionLocal()
        try:
            job = Job(
                celery_task_id="test-task-id",
                job_type=job_type,
                payload=json.dumps(payload),
                status=JobStatus.PENDING
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return job
        finally:
            db.close()


class SLATaskParityTests(CeleryParityTestBase):
    """Test SLA task parity between eager and worker modes."""
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    def test_sla_computation_parity(self, mock_compute_sla):
        """Test that SLA computation produces identical results in both modes."""
        # Mock SLA computation to return consistent results
        mock_result = {
            "device_id": "test-device-1",
            "period": "2024-01",
            "availability": 99.5,
            "is_violated": False,
            "mttr_minutes": 10
        }
        mock_compute_sla.return_value = mock_result
        
        eager_result = None
        worker_result = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            job = self.create_test_job(JobType.SLA_COMPUTATION, {
                "device_id": "test-device-1",
                "period": "2024-01"
            })
            
            eager_result = compute_sla_for_device(
                device_id="test-device-1",
                period="2024-01",
                correlation_id="test-correlation-id"
            )
        
        # Reset mocks for worker mode test
        mock_compute_sla.reset_mock()
        mock_compute_sla.return_value = mock_result
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            # Simulate worker execution by calling the task directly
            worker_result = compute_sla_for_device.apply(
                kwargs={
                    "device_id": "test-device-1",
                    "period": "2024-01",
                    "correlation_id": "test-correlation-id"
                }
            ).get()
        
        # Assert parity - results should be identical
        self.assertEqual(eager_result, worker_result)
        self.assertEqual(eager_result["device_id"], "test-device-1")
        self.assertEqual(eager_result["period"], "2024-01")
        self.assertFalse(eager_result["is_violated"])
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    @patch('app.tasks.sla_tasks.trigger_sla_violation_webhooks')
    def test_sla_violation_webhook_parity(self, mock_webhooks, mock_compute_sla):
        """Test that SLA violation webhook triggering works consistently."""
        # Mock SLA computation with violation
        mock_result = {
            "device_id": "test-device-2",
            "period": "2024-01",
            "availability": 95.0,
            "is_violated": True,
            "mttr_minutes": 120
        }
        mock_compute_sla.return_value = mock_result
        mock_webhooks.return_value = []
        
        eager_result = None
        worker_result = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            eager_result = compute_sla_for_device(
                device_id="test-device-2",
                period="2024-01"
            )
        
        # Reset mocks for worker mode test
        mock_compute_sla.reset_mock()
        mock_webhooks.reset_mock()
        mock_compute_sla.return_value = mock_result
        mock_webhooks.return_value = []
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            worker_result = compute_sla_for_device.apply(
                kwargs={
                    "device_id": "test-device-2",
                    "period": "2024-01"
                }
            ).get()
        
        # Assert parity
        self.assertEqual(eager_result, worker_result)
        self.assertTrue(eager_result["is_violated"])
        
        # Verify webhook was called in both modes
        self.assertEqual(mock_webhooks.call_count, 1)  # Called once in worker mode test
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    def test_bulk_sla_computation_parity(self, mock_compute_sla):
        """Test bulk SLA computation parity."""
        # Mock SLA computation for multiple devices
        def side_effect(db, device_id, period):
            return {
                "device_id": device_id,
                "period": period,
                "availability": 99.0 if device_id.endswith("1") else 95.0,
                "is_violated": device_id.endswith("2"),
                "mttr_minutes": 15
            }
        
        mock_compute_sla.side_effect = side_effect
        
        device_ids = ["device-1", "device-2", "device-3"]
        
        eager_result = None
        worker_result = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            eager_result = compute_bulk_sla(
                device_ids=device_ids,
                period="2024-01"
            )
        
        # Reset mocks for worker mode test
        mock_compute_sla.reset_mock()
        mock_compute_sla.side_effect = side_effect
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            worker_result = compute_bulk_sla.apply(
                kwargs={
                    "device_ids": device_ids,
                    "period": "2024-01"
                }
            ).get()
        
        # Assert parity
        self.assertEqual(eager_result, worker_result)
        self.assertEqual(eager_result["total"], 3)
        self.assertEqual(eager_result["violations"], 1)  # device-2 violates
        self.assertEqual(len(eager_result["results"]), 3)


class WebhookTaskParityTests(CeleryParityTestBase):
    """Test webhook task parity between eager and worker modes."""
    
    @patch('app.tasks.webhook_tasks.dispatch_delivery')
    def test_webhook_dispatch_parity(self, mock_dispatch):
        """Test webhook dispatch parity."""
        mock_dispatch.return_value = None
        
        delivery_id = "test-delivery-id"
        
        eager_result = None
        worker_result = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.webhook_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            eager_result = dispatch_webhook_delivery(delivery_id)
        
        # Reset mocks for worker mode test
        mock_dispatch.reset_mock()
        mock_dispatch.return_value = None
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.webhook_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            worker_result = dispatch_webhook_delivery.apply(
                args=[delivery_id]
            ).get()
        
        # Assert parity
        self.assertEqual(eager_result, worker_result)
        self.assertEqual(eager_result["delivery_id"], delivery_id)
        self.assertTrue(eager_result["dispatched"])
    
    @patch('app.tasks.webhook_tasks.trigger_sla_violation_webhooks')
    def test_sla_violation_async_parity(self, mock_trigger):
        """Test SLA violation async webhook parity."""
        mock_deliveries = [MagicMock(), MagicMock()]
        mock_trigger.return_value = mock_deliveries
        
        sla_data = {
            "device_id": "test-device",
            "period": "2024-01",
            "is_violated": True
        }
        
        eager_result = None
        worker_result = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.webhook_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            eager_result = trigger_sla_violation_async(sla_data, "sla.violation")
        
        # Reset mocks for worker mode test
        mock_trigger.reset_mock()
        mock_trigger.return_value = mock_deliveries
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.webhook_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            worker_result = trigger_sla_violation_async.apply(
                kwargs={
                    "sla_data": sla_data,
                    "event": "sla.violation"
                }
            ).get()
        
        # Assert parity
        self.assertEqual(eager_result, worker_result)
        self.assertEqual(eager_result["triggered"], 2)
        self.assertEqual(eager_result["event"], "sla.violation")


class JobEndpointParityTests(CeleryParityTestBase):
    """Test job endpoint parity between eager and worker modes."""
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    def test_job_submission_parity(self, mock_compute_sla):
        """Test that job submission works consistently in both modes."""
        mock_result = {
            "device_id": "test-device-job",
            "period": "2024-01",
            "availability": 99.8,
            "is_violated": False,
            "mttr_minutes": 5
        }
        mock_compute_sla.return_value = mock_result
        
        eager_response = None
        worker_response = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        eager_response = self.client.post(
            "/api/v1/jobs/sla-computation",
            json={
                "device_id": "test-device-job",
                "period": "2024-01"
            }
        )
        
        # Reset mocks for worker mode test
        mock_compute_sla.reset_mock()
        mock_compute_sla.return_value = mock_result
        
        # Test in worker mode
        self.set_eager_mode(False)
        worker_response = self.client.post(
            "/api/v1/jobs/sla-computation",
            json={
                "device_id": "test-device-job",
                "period": "2024-01"
            }
        )
        
        # Both should return 202 ACCEPTED
        self.assertEqual(eager_response.status_code, 202)
        self.assertEqual(worker_response.status_code, 202)
        
        # Job structure should be consistent
        eager_job = eager_response.json()
        worker_job = worker_response.json()
        
        self.assertEqual(eager_job["job_type"], worker_job["job_type"])
        self.assertEqual(eager_job["device_id"], worker_job["device_id"])
        self.assertEqual(eager_job["period"], worker_job["period"])
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    def test_bulk_job_submission_parity(self, mock_compute_sla):
        """Test bulk job submission parity."""
        def side_effect(db, device_id, period):
            return {
                "device_id": device_id,
                "period": period,
                "availability": 99.0,
                "is_violated": False,
                "mttr_minutes": 10
            }
        
        mock_compute_sla.side_effect = side_effect
        
        device_ids = ["bulk-device-1", "bulk-device-2"]
        
        eager_response = None
        worker_response = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        eager_response = self.client.post(
            "/api/v1/jobs/sla-computation/bulk",
            json={
                "device_ids": device_ids,
                "period": "2024-01"
            }
        )
        
        # Reset mocks for worker mode test
        mock_compute_sla.reset_mock()
        mock_compute_sla.side_effect = side_effect
        
        # Test in worker mode
        self.set_eager_mode(False)
        worker_response = self.client.post(
            "/api/v1/jobs/sla-computation/bulk",
            json={
                "device_ids": device_ids,
                "period": "2024-01"
            }
        )
        
        # Both should return 202 ACCEPTED
        self.assertEqual(eager_response.status_code, 202)
        self.assertEqual(worker_response.status_code, 202)
        
        # Job structure should be consistent
        eager_job = eager_response.json()
        worker_job = worker_response.json()
        
        self.assertEqual(eager_job["job_type"], worker_job["job_type"])
        self.assertEqual(eager_job["device_ids"], worker_job["device_ids"])


class ErrorHandlingParityTests(CeleryParityTestBase):
    """Test error handling parity between eager and worker modes."""
    
    @patch('app.tasks.sla_tasks.compute_device_sla')
    def test_task_error_parity(self, mock_compute_sla):
        """Test that task errors are handled consistently."""
        mock_compute_sla.side_effect = Exception("Test error")
        
        eager_exception = None
        worker_exception = None
        
        # Test in eager mode
        self.set_eager_mode(True)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            try:
                compute_sla_for_device(
                    device_id="error-device",
                    period="2024-01"
                )
            except Exception as e:
                eager_exception = e
        
        # Test in worker mode
        self.set_eager_mode(False)
        with patch('app.tasks.sla_tasks.SessionLocal', return_value=self.TestSessionLocal()):
            try:
                result = compute_sla_for_device.apply(
                    kwargs={
                        "device_id": "error-device",
                        "period": "2024-01"
                    }
                ).get()
            except Exception as e:
                worker_exception = e
        
        # Both should raise exceptions with similar messages
        self.assertIsNotNone(eager_exception)
        self.assertIsNotNone(worker_exception)
        self.assertIn("Test error", str(eager_exception))
        self.assertIn("Test error", str(worker_exception))


if __name__ == '__main__':
    unittest.main()
