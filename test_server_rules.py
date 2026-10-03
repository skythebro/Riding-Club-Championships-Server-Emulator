import unittest

from server_config import login_decision, ride_is_plausible


class LoginTests(unittest.TestCase):
    def test_local_defaults_accept_a_real_steam_id(self):
        allowed, reason = login_decision("76561198139908495", {"open_registration": True, "allowed_steam_ids": []}, False)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_allow_list_blocks_everyone_else(self):
        config = {"open_registration": True, "allowed_steam_ids": ["76561198000000001"]}
        allowed, reason = login_decision("76561198139908495", config, True)
        self.assertFalse(allowed)
        self.assertIn("invited", reason)

    def test_closed_registration_keeps_saved_accounts(self):
        config = {"open_registration": False, "allowed_steam_ids": []}
        allowed, _reason = login_decision("76561198139908495", config, True)
        self.assertTrue(allowed)
        blocked, reason = login_decision("76561198000000001", config, False)
        self.assertFalse(blocked)
        self.assertIn("new riders", reason)

    def test_garbage_id_is_refused(self):
        allowed, reason = login_decision("steam_error_1337", {"open_registration": True, "allowed_steam_ids": []}, False)
        self.assertFalse(allowed)
        self.assertIn("Steam", reason)


class RideTests(unittest.TestCase):
    def test_a_normal_round_is_kept(self):
        self.assertTrue(ride_is_plausible(48.2, 4, 8))

    def test_instant_time_is_dropped(self):
        self.assertFalse(ride_is_plausible(0.01, 0, 8))

    def test_absurd_faults_are_dropped(self):
        self.assertFalse(ride_is_plausible(40, 9999, 8))


if __name__ == "__main__":
    unittest.main()
