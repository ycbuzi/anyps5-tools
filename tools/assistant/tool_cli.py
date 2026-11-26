"""Runs one of the local AI's look-up tools from the command line, for Claude Code when Settings picks it as the engine.

python -I tool_cli.py <tool> '{"arg": "value"}'
Only tools that read and report are allowed; anything that builds, tests, writes, posts or deletes stays with the local AI.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant  # noqa: E402

READ_ONLY = {"pipeline_status", "pipeline_checkup", "whats_running", "running_now", "build_progress", "disk_space", "system_load",
             "game_list", "game_history", "diagnose_game", "crash_summary", "where_is_game_stuck", "frames_report",
             "search_game_logs", "missing_functions", "most_wanted_functions", "predicted_missing", "predicted_ranking", "module_copies", "district_status", "team_conversations",
             "fix_predictions", "why_function_missing", "find_prs", "pr_info", "prs_to_test", "search_github", "github_activity",
             "list_issues", "issue_info", "list_discussions", "discussion_info", "routine_check", "downloads_status",
             "fps_results", "speed_results", "fps_candidates", "recall", "knowledge", "claude_status", "keeper_status",
             "game_crash_code", "decompile_game_code", "how_game_calls", "game_strings", "read_game_code", "disassemble_game",
             "stack_callers", "find_function_code"}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in READ_ONLY or name not in assistant.TOOLS:
        print(f"{name or '(none)'} is not one of the look-up tools: {', '.join(sorted(READ_ONLY & set(assistant.TOOLS)))}")
        sys.exit(2)
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() else {}
    print(assistant.TOOLS[name][0](**args))
