import unittest

from services.build_week_mode import (
    DEMO_USER_ID_ENV,
    FORCE_WEB_ENV,
    is_ios_build_week_request,
    should_show_financial_calm_brief,
)


DEMO_USER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_USER_ID = "123e4567-e89b-12d3-a456-426614174001"
IOS_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)


def _auth(user_id: str = DEMO_USER_ID, **overrides) -> dict:
    auth = {
        "logged_in": True,
        "email": "judges-demo@example.invalid",
        "user_id": user_id,
        "access_token": "test-access-token",
    }
    auth.update(overrides)
    return auth


def _scope(owner_user_id: str = DEMO_USER_ID) -> dict:
    return {"owner_user_id": owner_user_id}


def _is_visible(
    *,
    cloud_auth=None,
    app_scope=None,
    query_params=None,
    user_agent: str = "",
    environ=None,
) -> bool:
    return should_show_financial_calm_brief(
        cloud_auth={} if cloud_auth is None else cloud_auth,
        app_scope={} if app_scope is None else app_scope,
        query_params={} if query_params is None else query_params,
        user_agent=user_agent,
        environ={} if environ is None else environ,
    )


class BuildWeekModeTests(unittest.TestCase):
    def test_default_is_hidden_for_public_website_users(self):
        self.assertFalse(_is_visible())

    def test_matching_demo_uuid_shows_on_regular_web(self):
        self.assertTrue(
            _is_visible(
                cloud_auth=_auth(),
                app_scope=_scope(),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_ordinary_authenticated_user_stays_on_legacy_brief(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(OTHER_USER_ID),
                app_scope=_scope(OTHER_USER_ID),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_demo_email_cannot_grant_access_for_a_different_uuid(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(OTHER_USER_ID, email="judges-demo@example.invalid"),
                app_scope=_scope(OTHER_USER_ID),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_demo_requires_logged_in_session_and_access_token(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(logged_in=False),
                app_scope=_scope(),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(access_token=""),
                app_scope=_scope(),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_demo_requires_finance_scope_owner_to_match_authenticated_uuid(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(),
                app_scope=_scope(OTHER_USER_ID),
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_demo_environment_accepts_one_uuid_not_an_allowlist(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(),
                app_scope=_scope(),
                environ={DEMO_USER_ID_ENV: f"{DEMO_USER_ID},{OTHER_USER_ID}"},
            )
        )

    def test_force_web_is_strict_opt_in_for_internal_preview(self):
        for true_value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=true_value):
                self.assertTrue(_is_visible(environ={FORCE_WEB_ENV: true_value}))

        for false_value in ("", "0", "false", "enabled", "2"):
            with self.subTest(value=false_value):
                self.assertFalse(_is_visible(environ={FORCE_WEB_ENV: false_value}))

    def test_f_w_alone_never_grants_access(self):
        self.assertFalse(
            _is_visible(
                query_params={"f_w": "1"},
                user_agent=IOS_USER_AGENT,
            )
        )

    def test_ios_app_request_is_detected_only_from_combined_request_signals(self):
        self.assertTrue(
            is_ios_build_week_request(
                {"f_w": "1", "f_shell": "1"},
                IOS_USER_AGENT,
            )
        )
        self.assertFalse(
            is_ios_build_week_request(
                {"f_w": "1", "f_shell": "1"},
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
            )
        )
        self.assertFalse(
            is_ios_build_week_request(
                {"f_w": "1"},
                IOS_USER_AGENT,
            )
        )

    def test_ios_app_hide_wins_over_demo_uuid(self):
        self.assertFalse(
            _is_visible(
                cloud_auth=_auth(),
                app_scope=_scope(),
                query_params={"f_w": "1", "f_shell": "1"},
                user_agent=IOS_USER_AGENT,
                environ={DEMO_USER_ID_ENV: DEMO_USER_ID},
            )
        )

    def test_ios_app_hide_wins_over_force_web(self):
        self.assertFalse(
            _is_visible(
                query_params={"f_w": "1", "f_shell": "1"},
                user_agent=IOS_USER_AGENT,
                environ={FORCE_WEB_ENV: "1"},
            )
        )

    def test_android_and_other_native_users_are_hidden_by_default(self):
        self.assertFalse(
            _is_visible(
                query_params={"f_w": "1", "f_shell": "1", "f_platform": "android"},
                user_agent="Mozilla/5.0 (Linux; Android 15; wv) Version/4.0 Chrome/131 Mobile",
            )
        )


if __name__ == "__main__":
    unittest.main()
