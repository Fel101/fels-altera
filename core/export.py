"""
Altera — Memory Export (Phase 8)

Export memories and life data in multiple formats: JSON, Markdown, PDF summary.
Creates a "life as Altera knows it" document of the user's personal knowledge base.
"""

import logging
import json
import os
from datetime import datetime

LOGGER = logging.getLogger(__name__)


class MemoryExporter:
    """Export Altera's memory and knowledge about the user in various formats."""

    def __init__(self):
        self._export_dir = None

    @property
    def export_dir(self):
        from core.paths import data_path
        path = self._export_dir or str(data_path('exports'))
        os.makedirs(path, exist_ok=True)
        return path

    @export_dir.setter
    def export_dir(self, value):
        self._export_dir = value

    def export_json(self, mem_mgr=None, event_timeline=None, relationship_graph=None):
        """Export all memory data as a structured JSON file.

        Returns dict with complete knowledge base.
        """
        if mem_mgr is None:
            from core.memory import memory_manager
            mem_mgr = memory_manager

        all_memories = mem_mgr.get_all_memories()

        # Group memories by type for easier navigation
        grouped = {}
        for mem in all_memories:
            mem_type = mem.get("metadata", {}).get("type", "fact")
            if mem_type not in grouped:
                grouped[mem_type] = []
            grouped[mem_type].append(mem)

        # Add relationship data
        relationships = {}
        if relationship_graph is None:
            from core.relationships import relationship_graph as rg
            relationships = rg.get_graph()

        # Add life events
        events = []
        if event_timeline is None:
            from core.events import event_timeline as et
            events = et.get_timeline(limit=100)

        export_data = {
            "exported_at": datetime.now().isoformat(),
            "version": "2.0",
            "summary": {
                "total_memories": len(all_memories),
                "memory_types": {k: len(v) for k, v in grouped.items()},
                "relationships_tracked": len(relationships),
                "life_events_tracked": len(events),
            },
            "memories_by_type": grouped,
            "all_memories": all_memories,
            "relationships": relationships,
            "life_events": events,
        }

        # Save to file
        filename = f"altera-knowledge-base-{datetime.now().strftime('%Y-%m-%d')}.json"
        filepath = os.path.join(self.export_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        return export_data, filepath

    def export_markdown(self, mem_mgr=None, event_timeline=None, relationship_graph=None):
        """Export all memory data as a readable Markdown document.

        Returns tuple of (markdown_content, filename).
        """
        if mem_mgr is None:
            from core.memory import memory_manager
            mem_mgr = memory_manager

        all_memories = mem_mgr.get_all_memories()
        relationships = {}
        if relationship_graph is None:
            from core.relationships import relationship_graph as rg
            relationships = rg.get_graph()

        events = []
        if event_timeline is None:
            from core.events import event_timeline as et
            events = et.get_timeline(limit=100)

        lines = []
        lines.append("# Altera's Knowledge About You")
        lines.append(f"\n*Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}*\n")
        lines.append("---\n")

        # Summary section
        total_memories = len(all_memories)
        high_conf = [m for m in all_memories if float(m.get("metadata", {}).get("confidence", 0)) > 0.8]
        lines.append("## Summary\n")
        lines.append(f"- **Total memories stored:** {total_memories}")
        lines.append(f"- **High-confidence facts (>80%):** {len(high_conf)}")
        lines.append(f"- **Relationships tracked:** {len(relationships)}")
        lines.append(f"- **Life events recorded:** {len(events)}\n")
        lines.append("---\n")

        # Memories by type
        grouped = {}
        for mem in all_memories:
            mem_type = mem.get("metadata", {}).get("type", "fact")
            if mem_type not in grouped:
                grouped[mem_type] = []
            grouped[mem_type].append(mem)

        type_icons = {
            "fact": "📌",
            "preference": "💡",
            "event": "📅",
        }

        for mem_type in ["fact", "preference", "event"]:
            memories = grouped.get(mem_type, [])
            if not memories:
                continue

            icon = type_icons.get(mem_type, "•")
            lines.append(f"## {icon} {mem_type.title()}s ({len(memories)})\n")

            for mem in sorted(memories, key=lambda m: float(m.get("metadata", {}).get("confidence", 0)), reverse=True):
                content = mem.get("content", "")
                meta = mem.get("metadata", {})
                confidence = float(meta.get("confidence", 0))
                created = meta.get("created_at", "unknown")

                # Format date nicely
                try:
                    dt = datetime.fromisoformat(created)
                    date_str = dt.strftime("%B %d, %Y")
                except (ValueError, TypeError):
                    date_str = created[:10] if len(str(created)) > 10 else created

                conf_bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))
                lines.append(f"- **{content}**")
                lines.append(f"  - Confidence: {conf_bar} ({confidence:.0%})")
                lines.append(f"  - Added: {date_str}")
                lines.append("")

        # Relationships section
        if relationships:
            people = {k: v for k, v in relationships.items() if v.get("type") == "person"}
            places = {k: v for k, v in relationships.items() if v.get("type") == "place"}
            orgs = {k: v for k, v in relationships.items() if v.get("type") == "organization"}

            lines.append("---\n")
            lines.append("## 👥 People You Know\n")
            for _name, data in sorted(people.items(), key=lambda x: x[1].get("mention_count", 0), reverse=True):
                mentions = data.get("mention_count", 0)
                first_seen = data.get("first_mentioned", "unknown")[:10] if len(str(data.get("first_mentioned", ""))) > 10 else str(data.get("first_mentioned", ""))
                lines.append(f"- **{data['name']}** — {mentions} mentions (since {first_seen})")

            if places:
                lines.append("\n## 📍 Places\n")
                for _name, data in sorted(places.items(), key=lambda x: x[1].get("mention_count", 0), reverse=True):
                    mentions = data.get("mention_count", 0)
                    lines.append(f"- **{data['name']}** — {mentions} mentions")

            if orgs:
                lines.append("\n## 🏢 Organizations\n")
                for _name, data in sorted(orgs.items(), key=lambda x: x[1].get("mention_count", 0), reverse=True):
                    mentions = data.get("mention_count", 0)
                    lines.append(f"- **{data['name']}** — {mentions} mentions")

        # Life events timeline
        if events:
            lines.append("\n---\n")
            lines.append("## 🎯 Life Events Timeline\n")
            for event in reversed(events):  # Oldest first
                detail = event.get("detail", "")
                etype = event.get("event_type", "unknown")
                timestamp = event.get("timestamp", "unknown")[:10] if len(str(event.get("timestamp", ""))) > 10 else str(event.get("timestamp", ""))

                type_icons_event = {
                    "birthday": "🎂",
                    "achievement": "🏆",
                    "trip": "✈️",
                    "work": "💼",
                    "relationship": "❤️",
                    "health": "🏥",
                    "holiday": "🎄",
                    "general_milestone": "⭐",
                }
                icon = type_icons_event.get(etype, "•")

                lines.append(f"- **{icon} [{etype}]** {detail} — *{timestamp}*")

        # User profile summary
        try:
            from core.profile import load_user_profile
            profile = load_user_profile()

            if profile.get("name"):
                lines.append("\n---\n")
                lines.append("## About You\n")
                lines.append(f"**Name:** {profile['name']}\n")

                hobbies = profile.get("hobbies", [])
                if hobbies:
                    lines.append(f"**Interests:** {', '.join(hobbies)}\n")

                preferences = profile.get("preferences", {})
                if preferences:
                    lines.append("**Preferences:**\n")
                    for key, value in preferences.items():
                        lines.append(f"- **{key}:** {value}")
        except Exception:
            LOGGER.exception("Error reading user profile")

        markdown_content = "\n".join(lines)

        filename = f"altera-knowledge-base-{datetime.now().strftime('%Y-%m-%d')}.md"
        filepath = os.path.join(self.export_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return markdown_content, filename

    def export_pdf_summary(self, mem_mgr=None, event_timeline=None, relationship_graph=None):
        """Export a PDF summary of the user's life as Altera knows it.

        Uses a simple HTML-to-PDF approach via wkhtmltopdf if available,
        otherwise falls back to generating an HTML document that can be printed.

        Returns tuple of (success: bool, filepath_or_error).
        """
        try:
            from core.memory import memory_manager as mm
            mem_mgr = mem_mgr or mm

            all_memories = mem_mgr.get_all_memories()

            relationships = {}
            if relationship_graph is None:
                from core.relationships import relationship_graph as rg
                relationships = rg.get_graph()

            events = []
            if event_timeline is None:
                from core.events import event_timeline as et
                events = et.get_timeline(limit=100)

            # Build HTML document styled like a report
            html_parts = []
            html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Altera's Knowledge About You</title>
<style>
    body { font-family: 'Georgia', serif; max-width: 800px; margin: 40px auto; padding: 20px; color: #333; line-height: 1.6; }
    h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
    h2 { color: #2980b9; margin-top: 30px; }
    .summary-box { background: #f8f9fa; border-left: 4px solid #3498db; padding: 15px; margin: 20px 0; border-radius: 4px; }
    .memory-item { padding: 8px 0; border-bottom: 1px solid #eee; }
    .confidence-bar { display: inline-block; background: #3498db; height: 6px; border-radius: 3px; margin-left: 8px; }
    table { width: 100%; border-collapse: collapse; margin: 15px 0; }
    th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }
    th { background: #f8f9fa; font-weight: bold; }
    .event-item { margin: 8px 0; padding-left: 20px; position: relative; }
    .event-item::before { content: '•'; color: #3498db; font-size: 1.5em; position: absolute; left: 0; top: -2px; }
    .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; text-align: center; color: #999; font-size: 0.9em; }
    @media print { body { margin: 20px; } .no-print { display: none; } }
</style>
</head>
<body>""")

            html_parts.append("<h1>🤖 Altera's Knowledge About You</h1>")
            html_parts.append(f'<p class="footer">Generated on {datetime.now().strftime("%B %d, %Y at %I:%M %p")} by Altera AI Companion</p>')

            # Summary box
            high_conf = [m for m in all_memories if float(m.get("metadata", {}).get("confidence", 0)) > 0.8]
            html_parts.append(f"""<div class="summary-box">
<h2>📊 Overview</h2>
<table>
<tr><td>Total Memories</td><td><strong>{len(all_memories)}</strong></td></tr>
<tr><td>High-Confidence Facts (>80%)</td><td><strong>{len(high_conf)}</strong></td></tr>
<tr><td>Relationships Tracked</td><td><strong>{len(relationships)}</strong></td></tr>
<tr><td>Life Events Recorded</td><td><strong>{len(events)}</strong></td></tr>
</table>
</div>""")

            # Memories by type
            grouped = {}
            for mem in all_memories:
                mem_type = mem.get("metadata", {}).get("type", "fact")
                if mem_type not in grouped:
                    grouped[mem_type] = []
                grouped[mem_type].append(mem)

            type_icons = {"fact": "📌", "preference": "💡", "event": "📅"}
            for mem_type in ["fact", "preference", "event"]:
                memories = grouped.get(mem_type, [])
                if not memories:
                    continue
                icon = type_icons.get(mem_type, "•")
                html_parts.append(f"<h2>{icon} {mem_type.title()}s ({len(memories)})</h2>")

                for mem in sorted(memories, key=lambda m: float(m.get("metadata", {}).get("confidence", 0)), reverse=True):
                    content = mem.get("content", "")
                    confidence = float(mem.get("metadata", {}).get("confidence", 0))
                    bar_width = int(confidence * 200)
                    html_parts.append(
                        f'<div class="memory-item"><strong>{content}</strong>'
                        f'<span class="confidence-bar" style="width:{bar_width}px; background:#3498db;"></span> '
                        f'({confidence:.0%})</div>'
                    )

            # Relationships
            if relationships:
                people = {k: v for k, v in relationships.items() if v.get("type") == "person"}
                html_parts.append("<h2>👥 People You Know</h2><table>")
                html_parts.append("<tr><th>Name</th><th>Mentions</th><th>Since</th></tr>")
                for _name, data in sorted(people.items(), key=lambda x: x[1].get("mention_count", 0), reverse=True):
                    first_seen = str(data.get("first_mentioned", "unknown"))[:10]
                    html_parts.append(f"<tr><td>{data['name']}</td><td>{data.get('mention_count', 0)}</td><td>{first_seen}</td></tr>")
                html_parts.append("</table>")

            # Life events
            if events:
                html_parts.append("<h2>🎯 Life Events Timeline</h2>")
                for event in reversed(events):
                    detail = event.get("detail", "")
                    etype = event.get("event_type", "unknown")
                    timestamp = str(event.get("timestamp", "unknown"))[:10]
                    html_parts.append(f'<div class="event-item"><strong>[{etype}]</strong> {detail} <em>({timestamp})</em></div>')

            from core.config import get_config
            from core.fels_suite import PORTS
            app_port = int((get_config().get('app') or {}).get('port', PORTS['altera']))
            html_parts.append(f"""<div class="footer">
<p>This document was generated by Altera AI Companion. All data is stored locally on your machine.</p>
<p>For questions or to update this information, use the Altera app at localhost:{app_port}</p>
</div></body></html>""")

            html_content = "\n".join(html_parts)

            # Try wkhtmltopdf first
            try:
                import subprocess
                filename = f"altera-knowledge-base-{datetime.now().strftime('%Y-%m-%d')}.pdf"
                filepath = os.path.join(self.export_dir, filename)

                result = subprocess.run(
                    ["wkhtmltopdf", "-", filepath],
                    input=html_content.encode("utf-8"),
                    capture_output=True,
                    timeout=30
                )

                if result.returncode == 0 and os.path.exists(filepath):
                    return True, filepath
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            # Fallback: save as HTML with print hint
            filename = f"altera-knowledge-base-{datetime.now().strftime('%Y-%m-%d')}.html"
            filepath = os.path.join(self.export_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            return True, filepath  # Return HTML path if PDF not available

        except Exception as e:
            LOGGER.exception("PDF export error")
            return False, str(e)


# Singleton instance
memory_exporter = MemoryExporter()
