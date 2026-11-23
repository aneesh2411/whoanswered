import unittest

from whoanswered.policy import InMemoryPlaybackSink, PlaybackAction, PolicyEvent, run_policy


class PolicyTest(unittest.TestCase):
    def run_script(self, *script):
        sink = InMemoryPlaybackSink()
        run_policy((PolicyEvent(event, at_ms) for event, at_ms in script), sink)
        return sink

    def test_reply_requires_screener_and_end_of_prompt(self):
        for script in (
            (("prompt_end", 20),),
            (("screener", 20), ("screener", 40)),
        ):
            with self.subTest(script=script):
                self.assertEqual(self.run_script(*script).trace, [])

        sink = self.run_script(
            ("prompt_end", 20),
            ("screener", 40),
            ("screener", 40),
            ("prompt_end", 100),
            ("prompt_end", 100),
            ("screener", 120),
            ("prompt_end", 160),
        )
        self.assertEqual(sink.trace, [PlaybackAction("reply_identity_and_purpose", 100)])

    def test_conversation_starts_at_human_takeover(self):
        sink = self.run_script(
            ("screener", 20),
            ("prompt_end", 40),
            ("human_takeover", 160),
            ("human_takeover", 180),
        )
        self.assertEqual(sink.trace, [
            PlaybackAction("reply_identity_and_purpose", 40),
            PlaybackAction("start_conversation", 160),
        ])

    def test_voicemail_starts_when_mailbox_is_ready(self):
        sink = self.run_script(
            ("screener", 20),
            ("prompt_end", 40),
            ("mailbox_ready", 200),
            ("mailbox_ready", 220),
        )
        self.assertEqual(sink.trace, [
            PlaybackAction("reply_identity_and_purpose", 40),
            PlaybackAction("play_voicemail", 200),
        ])

    def test_playback_routes_can_start_without_screening(self):
        for event, action in (
            ("human_takeover", "start_conversation"),
            ("mailbox_ready", "play_voicemail"),
        ):
            with self.subTest(event=event):
                sink = self.run_script((event, 50), ("screener", 60), ("prompt_end", 80))
                self.assertEqual(sink.trace, [PlaybackAction(action, 50)])

    def test_playback_actions_at_same_time_follow_script_order(self):
        sink = self.run_script(
            ("screener", 20),
            ("prompt_end", 100),
            ("human_takeover", 100),
            ("mailbox_ready", 100),
        )
        self.assertEqual(sink.trace, [
            PlaybackAction("reply_identity_and_purpose", 100),
            PlaybackAction("start_conversation", 100),
        ])

    def test_disconnect_cancels_pending_playback_and_ignores_later_events(self):
        screening = (("screener", 20), ("prompt_end", 40))
        reply = PlaybackAction("reply_identity_and_purpose", 40)
        self.assertEqual(self.run_script(*screening).pending, [reply])

        sink = self.run_script(
            *screening,
            ("disconnect", 70),
            ("human_takeover", 80),
            ("mailbox_ready", 90),
            ("disconnect", 100),
        )
        self.assertEqual(sink.trace, [
            reply,
            PlaybackAction("cancel_playback", 70),
        ])
        self.assertEqual(sink.pending, [])

    def test_disconnect_takes_priority_over_all_playback_triggers_at_same_time(self):
        for trigger in ("prompt_end", "human_takeover", "mailbox_ready"):
            for batch in (
                ((trigger, 100), ("disconnect", 100)),
                (("disconnect", 100), (trigger, 100)),
            ):
                with self.subTest(batch=batch):
                    sink = self.run_script(("screener", 20), *batch)
                    self.assertEqual(sink.trace, [PlaybackAction("cancel_playback", 100)])
                    self.assertEqual(sink.pending, [])


if __name__ == "__main__":
    unittest.main()
