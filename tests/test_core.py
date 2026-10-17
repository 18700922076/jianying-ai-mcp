from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jianying_ai_mcp.capabilities import capability_report
from jianying_ai_mcp.draft_crypto import classify_draft_bytes, load_draft_json
from jianying_ai_mcp.config import Settings, compatibility_notice
from jianying_ai_mcp.draft_ops import (
    ManifestError,
    analyze_reference_draft,
    batch_from_template,
    build_from_reference,
    build_manifest,
)
from jianying_ai_mcp.video_observer import (
    analyze_video,
    get_analysis_context,
    observer_status,
    parse_subtitle_file,
    save_observations,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "test_assets"
ENCRYPTED_REFERENCE = ROOT / "backups" / "AI剪辑兼容测试_20260814_基础版备份"


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".build").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".build", prefix="mcp-test-")
        self.root = Path(self.temp.name)
        self.drafts = self.root / "drafts"
        self.work = self.root / "work"
        self.drafts.mkdir()
        detected = Settings.from_env()
        self.settings = Settings(
            self.drafts,
            self.work,
            self.root / "JianyingPro" / "10.0.5.13816" / "JianyingPro.exe",
            detected.ffmpeg_exe,
            detected.ffprobe_exe,
            detected.yt_dlp_exe,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manifest(self, name: str = "集成测试") -> dict:
        return {
            "draft": {"name": name, "width": 1280, "height": 720, "fps": 30},
            "tracks": [
                {
                    "type": "video",
                    "name": "主画面",
                    "segments": [
                        {
                            "source": str(ASSETS / "compat_test_video.mp4"),
                            "start": "0s",
                            "duration": "4s",
                            "source_start": "0s",
                            "source_duration": "4s",
                            "filter": {"name": "_1980", "intensity": 30},
                            "animations": [{"category": "video_intro", "name": "渐显", "duration": "0.5s"}],
                            "keyframes": [
                                {"property": "uniform_scale", "time": "0s", "value": 0.9},
                                {"property": "uniform_scale", "time": "3.8s", "value": 1.0},
                            ],
                        }
                    ],
                },
                {
                    "type": "audio",
                    "name": "配乐",
                    "segments": [
                        {
                            "source": str(ASSETS / "compat_test_music.wav"),
                            "start": "0s",
                            "duration": "4s",
                            "volume": 0.25,
                            "fade": {"in": "0.5s", "out": "0.5s"},
                        }
                    ],
                },
                {
                    "type": "text",
                    "name": "标题",
                    "segments": [
                        {
                            "text": "批处理 MCP 测试",
                            "start": "0s",
                            "duration": "2s",
                            "style": {"size": 8, "bold": True, "align": 1},
                            "border": {"color": [0, 0, 0], "width": 20},
                            "clip": {"transform_y": 0.65},
                        }
                    ],
                },
            ],
            "srt_imports": [
                {
                    "source": str(ASSETS / "captions.srt"),
                    "track_name": "字幕",
                    "style": {"size": 5, "align": 1, "auto_wrapping": True},
                    "clip": {"transform_y": -0.8},
                }
            ],
        }

    def test_capabilities_and_version(self) -> None:
        self.assertIn("create_draft", capability_report()["verified"])
        self.assertTrue(compatibility_notice(self.settings)["matches_tested_version"])

    def test_manifest_dry_run_and_publish(self) -> None:
        preview = build_manifest(self.settings, self.manifest(), dry_run=True)
        self.assertTrue(preview["success"])
        self.assertIsNone(preview["draft_path"])
        result = build_manifest(self.settings, self.manifest(), dry_run=False)
        output = Path(result["draft_path"])
        self.assertTrue((output / "draft_content.json").is_file())
        content = json.loads((output / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(len(content["tracks"]), 4)
        with self.assertRaises(FileExistsError):
            build_manifest(self.settings, self.manifest(), dry_run=False)

    def test_reference_analysis_replace_and_batch(self) -> None:
        template_name = "参考模板"
        build_manifest(self.settings, self.manifest(template_name), dry_run=False)
        profile = analyze_reference_draft(self.settings, template_name, include_text=True)
        self.assertTrue(profile["success"])
        self.assertGreaterEqual(len(profile["profile"]["tracks"]), 3)

        result = build_from_reference(
            self.settings,
            template_name,
            "参考生成",
            replacements=[
                {
                    "track_type": "video",
                    "track_name": "主画面",
                    "segment_index": 0,
                    "source": str(ASSETS / "compat_test_video_alt.mp4"),
                }
            ],
            text_replacements=[
                {"track_name": "标题", "segment_index": 0, "text": "参考蓝图生成"}
            ],
            dry_run=True,
        )
        self.assertTrue(result["success"])

        batch = batch_from_template(
            self.settings,
            template_name,
            [
                {"name": "批量A", "text_replacements": [{"track_name": "标题", "segment_index": 0, "text": "A"}]},
                {"name": "批量B", "text_replacements": [{"track_name": "标题", "segment_index": 0, "text": "B"}]},
            ],
            dry_run=True,
        )
        self.assertEqual(batch["count"], 2)

    def test_draft_format_detection_and_missing_reader_error(self) -> None:
        self.assertEqual(classify_draft_bytes(b'{"tracks":[]}'), "plaintext_json")
        self.assertEqual(classify_draft_bytes(b"not-json"), "unknown")
        encrypted = ENCRYPTED_REFERENCE / "draft_content.json"
        if not encrypted.is_file():
            self.skipTest("没有加密草稿样本")
        self.assertEqual(classify_draft_bytes(encrypted.read_bytes()), "jianying_encrypted")
        target = self.drafts / "加密参考"
        target.mkdir()
        (target / "draft_content.json").write_bytes(encrypted.read_bytes())
        with self.assertRaises(ManifestError):
            analyze_reference_draft(self.settings, "加密参考")

    def test_plaintext_loader_reports_format(self) -> None:
        source = self.root / "plain.json"
        source.write_text('{"tracks":[],"materials":{}}', encoding="utf-8")
        loaded = load_draft_json(self.settings, source)
        self.assertEqual(loaded.source_format, "plaintext_json")
        self.assertEqual(loaded.content["tracks"], [])

    def test_unverified_features_rejected(self) -> None:
        manifest = self.manifest("拒绝测试")
        manifest["tracks"][0]["segments"][0]["mask"] = {"type": "圆形"}
        with self.assertRaises(ManifestError):
            build_manifest(self.settings, manifest, dry_run=True)

    def test_video_observer_subtitles_frames_cache_and_observations(self) -> None:
        status = observer_status(self.settings)
        if not status["available"]:
            self.skipTest("本机没有 ffmpeg/ffprobe")

        parsed = parse_subtitle_file(ASSETS / "captions.srt")
        self.assertGreaterEqual(len(parsed), 1)
        self.assertIn("start_ms", parsed[0])
        vtt = self.root / "sample.vtt"
        vtt.write_text("WEBVTT\n\n00:01.250 --> 00:02.500\n测试 VTT\n", encoding="utf-8")
        self.assertEqual(parse_subtitle_file(vtt)[0]["start_ms"], 1250)

        report = analyze_video(
            self.settings,
            str((ASSETS / "compat_test_video.mp4").resolve()),
            detail="efficient",
            max_frames=5,
            resolution=160,
            subtitle_path=str((ASSETS / "captions.srt").resolve()),
            transcription="none",
        )
        self.assertTrue(report["success"])
        self.assertGreater(report["frame_count"], 0)
        self.assertTrue(Path(report["transcript_srt_path"]).is_file())
        self.assertTrue(all(Path(item["path"]).is_file() for item in report["frames"]))

        cached = analyze_video(
            self.settings,
            str((ASSETS / "compat_test_video.mp4").resolve()),
            detail="efficient",
            max_frames=5,
            resolution=160,
            subtitle_path=str((ASSETS / "captions.srt").resolve()),
            transcription="none",
        )
        self.assertTrue(cached["cache_hit"])

        observed = save_observations(
            self.settings,
            report["analysis_id"],
            [{"time_ms": report["frames"][0]["time_ms"], "description": "测试代表帧"}],
            [{"start_ms": 0, "end_ms": min(1000, report["range"]["end_ms"]), "reason": "测试片段"}],
        )
        self.assertTrue(Path(observed["observations_path"]).is_file())
        context = get_analysis_context(self.settings, report["analysis_id"], limit=1)
        self.assertLessEqual(len(context["transcript"]), 1)
        self.assertIn("transcript_page", context)

        scene_report = analyze_video(
            self.settings,
            str((ASSETS / "compat_test_video.mp4").resolve()),
            detail="balanced",
            max_frames=4,
            resolution=160,
            subtitle_path=str((ASSETS / "captions.srt").resolve()),
            transcription="none",
        )
        self.assertGreater(scene_report["frame_count"], 0)


if __name__ == "__main__":
    unittest.main()
