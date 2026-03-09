"""
Unit tests for BrassTrayIdList_Complete_APIView.
Verifies that scanned-rejected tray IDs are:
  - marked rejected_tray=True in the response
  - excluded from accepted_tray_ids
  - included in rejected_tray_ids
  - appended when they only exist in Brass_Audit_Rejected_TrayScan
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from Brass_QC.models import BrassTrayId
from BrassAudit.models import Brass_Audit_Rejected_TrayScan, Brass_Audit_Rejection_Table
from BrassAudit.views import BrassTrayIdList_Complete_APIView
import json


class BrassTrayIdListCompleteAPIViewTest(TestCase):
    """Test the CompleteTable (eye-icon) endpoint handles scanned-rejected trays correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='testuser', password='testpass')
        cls.lot_id = 'LID_TEST_001'

        # Create BrassTrayId rows (DB-level trays)
        BrassTrayId.objects.create(lot_id=cls.lot_id, tray_id='NB-T001', tray_quantity=16, top_tray=True, rejected_tray=False, delink_tray=False)
        BrassTrayId.objects.create(lot_id=cls.lot_id, tray_id='NB-T002', tray_quantity=16, top_tray=False, rejected_tray=False, delink_tray=False)
        BrassTrayId.objects.create(lot_id=cls.lot_id, tray_id='NB-T003', tray_quantity=16, top_tray=False, rejected_tray=False, delink_tray=True)

        # Create rejection reason
        cls.reason = Brass_Audit_Rejection_Table.objects.create(rejection_reason='TEST DEFECT')

        # Scanned-rejected: NB-T001 was later scanned as rejected
        Brass_Audit_Rejected_TrayScan.objects.create(
            lot_id=cls.lot_id, rejected_tray_id='NB-T001',
            rejected_tray_quantity='16', rejection_reason=cls.reason, user=cls.user
        )
        # Scanned-rejected: NB-T999 only exists in scan table (new tray)
        Brass_Audit_Rejected_TrayScan.objects.create(
            lot_id=cls.lot_id, rejected_tray_id='NB-T999',
            rejected_tray_quantity='10', rejection_reason=cls.reason, user=cls.user
        )

    def _call_endpoint(self):
        factory = RequestFactory()
        request = factory.get(f'/brass_audit/brass_CompleteTable_tray_id_list/?lot_id={self.lot_id}')
        request.user = self.user
        view = BrassTrayIdList_Complete_APIView()
        response = view.get(request)
        return json.loads(response.content)

    def test_scanned_rejected_tray_marked_rejected(self):
        """NB-T001 (DB rejected_tray=False but scanned-rejected) should be rejected_tray=True."""
        data = self._call_endpoint()
        tray = next(t for t in data['trays'] if t['tray_id'] == 'NB-T001')
        self.assertTrue(tray['rejected_tray'])

    def test_scanned_rejected_not_top_tray(self):
        """NB-T001 (scanned-rejected) should NOT be is_top_tray."""
        data = self._call_endpoint()
        tray = next(t for t in data['trays'] if t['tray_id'] == 'NB-T001')
        self.assertFalse(tray['is_top_tray'])

    def test_non_rejected_becomes_top_tray(self):
        """NB-T002 (non-rejected, non-delinked) should be top tray."""
        data = self._call_endpoint()
        tray = next(t for t in data['trays'] if t['tray_id'] == 'NB-T002')
        self.assertTrue(tray['is_top_tray'])

    def test_scanned_only_tray_appears(self):
        """NB-T999 (only in Brass_Audit_Rejected_TrayScan) should appear in trays."""
        data = self._call_endpoint()
        tray_ids = [t['tray_id'] for t in data['trays']]
        self.assertIn('NB-T999', tray_ids)
        tray = next(t for t in data['trays'] if t['tray_id'] == 'NB-T999')
        self.assertTrue(tray['rejected_tray'])

    def test_rejected_tray_ids_in_summary(self):
        """rejection_summary.rejected_tray_ids should contain both NB-T001 and NB-T999."""
        data = self._call_endpoint()
        rejected_ids = data['rejection_summary']['rejected_tray_ids']
        self.assertIn('NB-T001', rejected_ids)
        self.assertIn('NB-T999', rejected_ids)

    def test_accepted_tray_ids_exclude_scanned_rejected(self):
        """accepted_tray_ids should NOT contain NB-T001 or NB-T999."""
        data = self._call_endpoint()
        accepted_ids = data['rejection_summary']['accepted_tray_ids']
        self.assertNotIn('NB-T001', accepted_ids)
        self.assertNotIn('NB-T999', accepted_ids)

    def test_backward_compatible_data_key(self):
        """Response should include 'data' alias for backward compatibility."""
        data = self._call_endpoint()
        self.assertIn('data', data)
        self.assertEqual(data['data'], data['trays'])

    def test_backward_compatible_summary_key(self):
        """Response should include 'summary' alias for backward compatibility."""
        data = self._call_endpoint()
        self.assertIn('summary', data)
        self.assertEqual(data['summary'], data['rejection_summary'])
