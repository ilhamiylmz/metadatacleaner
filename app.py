#!/usr/bin/env python3
"""
app.py - Terminal (CLI) & GUI application to inspect, edit, and remove all
image metadata (including AI / C2PA Content Credentials, SynthID provenance,
IPTC/8BIM social tracking tags, EXIF, XMP, and comments) for images in the
"process" folder.

Usage Examples:
  python3 app.py                     # Show all metadata for "process/" + interactive menu
  python3 app.py show                # Print full metadata report for "process/" and exit
  python3 app.py show process/a1-wallpaper-f0rf0xsakeffs.jpg
  python3 app.py --gui               # Launch Desktop GUI (GTK3, with Web GUI fallback)
  python3 app.py --web               # Launch local Web GUI in browser
  python3 app.py strip all           # Remove all metadata from all images in "process/"
  python3 app.py strip process/a1-wallpaper-f0rf0xsakeffs.jpg --backup
  python3 app.py edit process/a1-wallpaper-f0rf0xsakeffs.jpg
  python3 app.py edit process/857340270_18344740396256249_8853371540543943019_n.jpg --set iptc.SpecialInstructions="" --set exif.Artist="Theresa"
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from metadata_engine import (
    analyze_image,
    format_size,
    get_editable_fields,
    list_images_in_folder,
    strip_all_metadata,
    update_image_metadata,
)
from gui import run_gtk_gui, run_web_gui

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.tree import Tree
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None


DEFAULT_PROCESS_DIR = Path(__file__).resolve().parent / "process"


# ============================================================================
# Rich Terminal Rendering
# ============================================================================

def render_summary_table(reports: List[Dict[str, Any]]) -> None:
    """Print an overview table of all images analyzed."""
    if not HAS_RICH:
        print("\n=== SUMMARY OF IMAGES ===")
        for r in reports:
            fi = r["file_info"]
            ai = r["ai_summary"]
            print(f"- {fi['filename']} ({fi['dimensions']}, {fi['size_human']}): {ai['verdict']}")
        return

    tbl = Table(
        title="Images in 'process' Folder — Metadata & AI Provenance Summary",
        box=box.ROUNDED,
        show_lines=True,
    )
    tbl.add_column("#", style="cyan", justify="right", width=3)
    tbl.add_column("Filename", style="bold white", overflow="fold")
    tbl.add_column("Dimensions", style="magenta", width=15)
    tbl.add_column("Size", style="green", width=12)
    tbl.add_column("AI / Metadata Verdict", overflow="fold")
    tbl.add_column("Detected Tags / Badges", overflow="fold")

    for idx, rep in enumerate(reports, 1):
        fi = rep["file_info"]
        ai = rep["ai_summary"]
        if ai["is_ai_generated"]:
            v_style = f"[bold red]{ai['verdict']}[/bold red]"
        elif ai["has_social_tracking"] or ai["has_any_metadata"]:
            v_style = f"[bold yellow]{ai['verdict']}[/bold yellow]"
        else:
            v_style = f"[bold green]{ai['verdict']}[/bold green]"

        badges = ", ".join(ai["badges"]) if ai["badges"] else "[dim]None[/dim]"
        tbl.add_row(
            str(idx),
            fi["filename"],
            fi["dimensions"],
            fi["size_human"].split(" (")[0],
            v_style,
            badges,
        )

    console.print()
    console.print(tbl)


def render_image_full_report(rep: Dict[str, Any]) -> None:
    """Print a detailed metadata report for a single image (including AI/C2PA tags)."""
    if not HAS_RICH:
        from gui import format_overview_text, format_c2pa_text, format_standard_meta_text, format_segments_text
        print(format_overview_text(rep))
        if rep.get("c2pa", {}).get("present"):
            print(format_c2pa_text(rep))
        print(format_standard_meta_text(rep))
        print(format_segments_text(rep))
        return

    fi = rep["file_info"]
    ai = rep["ai_summary"]

    # 1. Header & Technical Info + AI Verdict Panel
    border_color = "red" if ai["is_ai_generated"] else ("yellow" if (ai["has_social_tracking"] or ai["has_any_metadata"]) else "green")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("File Path:", fi["path"])
    grid.add_row("File Size:", fi["size_human"])
    grid.add_row("SHA-256:", fi["sha256"])
    grid.add_row("Last Modified:", fi["modified_time"])
    grid.add_row("Image Format:", f"{fi['format']} — {fi.get('encoding_mode', '')}")
    grid.add_row("Dimensions:", f"{fi['dimensions']} ({fi.get('bit_depth', 0)}-bit, {fi.get('channels', 0)} channels)")
    if fi.get("subsampling"):
        grid.add_row("Chroma Subsampling:", fi["subsampling"])
    grid.add_row(
        "Verdict:",
        f"[bold {border_color}]{ai['verdict']}[/bold {border_color}]",
    )
    grid.add_row(
        "Detected Badges:",
        ", ".join(f"[bold reverse] {b} [/bold reverse]" for b in ai["badges"]) if ai["badges"] else "[dim]None[/dim]",
    )

    if ai.get("details"):
        for k, v in ai["details"].items():
            if isinstance(v, list):
                grid.add_row(f"{k}:", "\n".join(f"• {item}" for item in v))
            elif isinstance(v, dict):
                grid.add_row(f"{k}:", "\n".join(f"• {sk}: {sv}" for sk, sv in v.items()))
            else:
                grid.add_row(f"{k}:", str(v))

    console.print()
    console.print(
        Panel(
            grid,
            title=f"[bold white]📷 {fi['filename']}[/bold white]",
            subtitle=f"[bold {border_color}]{ai['verdict']}[/bold {border_color}]",
            border_style=border_color,
            box=box.DOUBLE_EDGE,
        )
    )

    # 2. C2PA Content Credentials & AI Provenance Tree (if present)
    c2pa = rep.get("c2pa", {})
    if c2pa.get("present"):
        tree = Tree(
            f"[bold red]🤖 C2PA / JUMBF Content Credentials Store[/bold red] "
            f"(Active Manifest: [bold yellow]{c2pa.get('active_manifest_id')}[/bold yellow])"
        )
        for m_idx, m in enumerate(c2pa.get("manifests", []), 1):
            role = "[bold green]ACTIVE MANIFEST[/bold green]" if m.get("is_active_manifest") else f"[cyan]INGREDIENT MANIFEST #{m_idx}[/cyan]"
            m_node = tree.add(f"{role} — [bold]{m.get('manifest_id')}[/bold]")

            claim = m.get("claim", {})
            cgi = claim.get("claim_generator_info", {})
            if isinstance(cgi, dict):
                m_node.add(f"[bold]Claim Generator:[/bold] {cgi.get('name')} (v{cgi.get('version')})")
            m_node.add(f"[bold]Instance ID:[/bold] {claim.get('instanceID')}  |  [bold]Hash Alg:[/bold] {claim.get('alg')}")

            if m.get("actions"):
                act_branch = m_node.add("[bold yellow]AI & Provenance Actions (`c2pa.actions.v2`):[/bold yellow]")
                for a in m["actions"]:
                    line = f"[bold white]{a.get('action')}[/bold white]"
                    if a.get("description"):
                        line += f" — [bold green]\"{a.get('description')}\"[/bold green]"
                    if a.get("digitalSourceType"):
                        line += f"\n[dim]DigitalSourceType:[/dim] {a.get('digitalSourceTypeLabel')} ({a.get('digitalSourceType')})"
                    act_branch.add(line)

            if m.get("ingredients"):
                ing_branch = m_node.add("[bold cyan]Ingredients & Validation (`c2pa.ingredient.v3`):[/bold cyan]")
                for ing in m["ingredients"]:
                    txt = f"{ing.get('label')}: relationship=[bold]{ing.get('relationship')}[/bold], desc={ing.get('description')!r}"
                    if "validation_codes" in ing:
                        txt += f"\n[green]Validated ({ing.get('validation_success_count')} passed):[/green] {', '.join(ing['validation_codes'])}"
                    ing_branch.add(txt)

            sig = m.get("signature", {})
            if sig:
                sig_branch = m_node.add("[bold magenta]Cryptographic Signature (`c2pa.signature` COSE_Sign1):[/bold magenta]")
                sig_branch.add(f"Algorithm: {sig.get('algorithm')}  |  Timestamp: [bold]{sig.get('timestamp_utc', 'N/A')}[/bold]")
                if sig.get("timestamp_authority"):
                    sig_branch.add(f"Timestamp Authority: {sig.get('timestamp_authority')}")
                for cert in sig.get("certificates", []):
                    sig_branch.add(
                        f"[bold]{cert.get('role')}:[/bold] {cert.get('subject')}\n"
                        f"[dim]Issuer:[/dim] {cert.get('issuer')}  |  [dim]Valid:[/dim] {cert.get('valid_from')} → {cert.get('valid_until')}"
                    )

        console.print(Panel(tree, border_style="red", box=box.ROUNDED))

    # 3. IPTC / Photoshop 8BIM / EXIF / XMP / Comments Table
    meta_tbl = Table(title=f"Standard & Embedded Metadata Tags ({fi['filename']})", box=box.SIMPLE_HEAVY, show_lines=False)
    meta_tbl.add_column("Section", style="bold cyan", width=18)
    meta_tbl.add_column("Tag / Field Name", style="bold yellow", width=30)
    meta_tbl.add_column("Value", style="white", overflow="fold")

    row_count = 0
    for k, v in rep.get("jfif", {}).items():
        meta_tbl.add_row("JFIF (APP0)", k, str(v))
        row_count += 1

    iptc_sec = rep.get("iptc", {})
    for r in iptc_sec.get("resources", []):
        meta_tbl.add_row("Photoshop 8BIM", f"Resource {r['id']} ({r['name']})", f"{r['size']} bytes")
        row_count += 1
    for k, v in iptc_sec.get("iptc", {}).items():
        meta_tbl.add_row("IPTC (APP13)", f"{k}", str(v))
        row_count += 1

    exif_sec = rep.get("exif", {})
    for sub in ("IFD0", "ExifIFD", "GPSIFD", "IFD1"):
        for k, v in exif_sec.get(sub, {}).items():
            meta_tbl.add_row(f"EXIF ({sub})", k, str(v))
            row_count += 1

    for k, v in rep.get("xmp", {}).get("fields", {}).items():
        meta_tbl.add_row("XMP (APP1)", k, str(v))
        row_count += 1

    for idx, c in enumerate(rep.get("comments", []), 1):
        meta_tbl.add_row("Comment (COM)", f"Comment #{idx}", c)
        row_count += 1

    for k, v in rep.get("png_chunks", {}).items():
        meta_tbl.add_row("PNG Text Chunk", k, str(v))
        row_count += 1

    if row_count > 0:
        console.print(meta_tbl)

    # 4. Compact Segment Map
    seg_summary = ", ".join(f"{s['marker']} ({s['name'].split(' ')[0]}: {s['length']}B)" for s in rep.get("segments", []))
    console.print(f"  [dim]Binary Segments:[/dim] {seg_summary}")


# ============================================================================
# Interactive CLI Editor & Menu
# ============================================================================

def interactive_edit_image(img_path: Path) -> None:
    """Interactive terminal workflow to edit or clear current metadata fields on an image."""
    rep = analyze_image(img_path)
    editable = get_editable_fields(rep)

    print(f"\n=== EDIT METADATA FOR: {img_path.name} ===")
    flat_items: List[Tuple[str, str, str]] = []
    for cat in ("iptc", "exif", "xmp", "comment", "c2pa"):
        for k, v in editable.get(cat, {}).items():
            flat_items.append((cat, k, str(v)))

    # Show numbered list of all editable fields (highlighting populated ones first)
    print("\nCurrent & Standard Editable Fields:")
    for i, (cat, k, v) in enumerate(flat_items, 1):
        status = f" = {v!r}" if v else " (empty)"
        marker = "*" if v else " "
        print(f"  {marker} [{i:2d}] {cat.upper():<7} . {k:<38}{status}")

    print("\nOptions:")
    print("  • Enter field number (1-{}) to edit or clear that field".format(len(flat_items)))
    if rep.get("c2pa", {}).get("present"):
        print("  • Enter 'c2pa-rm' to remove the entire C2PA / AI Content Credentials segment")
    print("  • Enter 's' to save changes and return")
    print("  • Enter 'q' to cancel without saving")

    edits: Dict[str, Dict[str, Any]] = {"exif": {}, "iptc": {}, "xmp": {}, "comment": {}, "c2pa": {}}
    remove_c2pa = False

    while True:
        choice = input("\nEdit choice (number / 's' to save / 'q' to cancel): ").strip()
        if choice.lower() == "q":
            print("[*] Cancelled edits.")
            return
        if choice.lower() == "s":
            res = update_image_metadata(img_path, edits, remove_c2pa=remove_c2pa)
            print(f"[+] Saved updated metadata to {res['path']} ({format_size(res['size'])}).")
            return
        if choice.lower() == "c2pa-rm":
            remove_c2pa = True
            print("[!] Marked entire C2PA / JUMBF AI Content Credentials segment for removal on save.")
            continue
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(flat_items):
                cat, k, cur_v = flat_items[idx]
                print(f"  Current value of {cat}.{k}: {cur_v!r}")
                new_v = input(f"  New value for {cat}.{k} (leave blank to clear/delete tag): ")
                edits[cat][k] = new_v
                flat_items[idx] = (cat, k, new_v)
                print(f"  -> Staged {cat}.{k} = {new_v!r}")
            else:
                print("[!] Invalid field number.")
        else:
            print("[!] Unknown input.")


def run_interactive_cli(folder: Path) -> None:
    """Run the interactive terminal loop after displaying the initial folder report."""
    while True:
        files = list_images_in_folder(folder)
        reports = [analyze_image(f) for f in files]
        render_summary_table(reports)

        print("\n======================================================================")
        print(" INTERACTIVE MENU — Image Metadata & AI Tag Tool")
        print("======================================================================")
        print("  [1] Show FULL metadata & AI/C2PA report for ALL images in 'process'")
        print("  [2] Inspect a single image in detail")
        print("  [3] Edit metadata of an image (modify/add/clear tags)")
        print("  [4] Remove ALL metadata from a single image")
        print("  [5] Remove ALL metadata from ALL images in 'process'")
        print("  [6] Launch Desktop GUI (GTK3 Window / Web GUI)")
        print("  [0] Exit")

        try:
            ans = input("\nSelect an option [0-6]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if ans == "0" or ans.lower() in ("q", "exit", "quit"):
            break
        elif ans == "1":
            for r in reports:
                render_image_full_report(r)
        elif ans == "2":
            for i, f in enumerate(files, 1):
                print(f"  [{i}] {f.name}")
            sel = input(f"Select image [1-{len(files)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(files):
                render_image_full_report(reports[int(sel) - 1])
        elif ans == "3":
            for i, f in enumerate(files, 1):
                print(f"  [{i}] {f.name}")
            sel = input(f"Select image to edit [1-{len(files)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(files):
                interactive_edit_image(files[int(sel) - 1])
        elif ans == "4":
            for i, f in enumerate(files, 1):
                print(f"  [{i}] {f.name}")
            sel = input(f"Select image to strip [1-{len(files)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(files):
                target = files[int(sel) - 1]
                bak = input("Keep a .bak backup file? [y/N]: ").strip().lower() == "y"
                if bak:
                    shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
                res = strip_all_metadata(target)
                print(f"[+] Removed {len(res['removed_segments'])} metadata segment(s) ({res['bytes_saved']:,} bytes saved) from {target.name}:")
                for seg in res["removed_segments"]:
                    print(f"    - {seg}")
        elif ans == "5":
            confirm = input(f"Remove ALL metadata from all {len(files)} images in '{folder.name}'? [y/N]: ").strip().lower()
            if confirm == "y":
                bak = input("Keep .bak backup files? [y/N]: ").strip().lower() == "y"
                for f in files:
                    if bak:
                        shutil.copy2(f, f.with_suffix(f.suffix + ".bak"))
                    res = strip_all_metadata(f)
                    print(f"[+] {f.name}: removed {len(res['removed_segments'])} segment(s) ({res['bytes_saved']:,} bytes saved)")
        elif ans == "6":
            print("[*] Launching GUI...")
            if not run_gtk_gui(folder):
                run_web_gui(folder)


# ============================================================================
# CLI Argument Parsing & Entrypoint
# ============================================================================

def apply_cli_edits(
    target: Path,
    set_pairs: List[str],
    clear_keys: List[str],
    remove_c2pa: bool,
    output_path: Optional[Path] = None,
) -> None:
    edits: Dict[str, Dict[str, Any]] = {"exif": {}, "iptc": {}, "xmp": {}, "comment": {}, "c2pa": {}}

    def resolve_cat_key(raw_key: str) -> Tuple[str, str]:
        if "." in raw_key:
            c, k = raw_key.split(".", 1)
            c_low = c.lower()
            if c_low in edits:
                return c_low, k
        # Auto-detect category if not prefixed
        rep = analyze_image(target)
        ed = get_editable_fields(rep)
        for cat, fields in ed.items():
            if raw_key in fields:
                return cat, raw_key
        return "exif", raw_key

    for item in set_pairs:
        if "=" not in item:
            print(f"[!] Invalid --set format {item!r}. Expected KEY=VALUE (e.g. exif.Artist='Jane' or iptc.SpecialInstructions='')")
            sys.exit(1)
        k_raw, val = item.split("=", 1)
        cat, k = resolve_cat_key(k_raw.strip())
        edits[cat][k] = val

    for k_raw in clear_keys:
        cat, k = resolve_cat_key(k_raw.strip())
        edits[cat][k] = ""

    res = update_image_metadata(target, edits, output_path=output_path, remove_c2pa=remove_c2pa)
    print(f"[+] Updated metadata on {res['path']} (new size: {format_size(res['size'])}).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect, edit, or remove all metadata (including AI/C2PA & SynthID tags) from images in the 'process' folder."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        choices=["show", "menu", "strip", "edit", "gui", "web"],
        help="Command to run: 'show' (show metadata), 'menu' (interactive CLI), 'strip' (remove metadata), 'edit' (edit metadata), 'gui' (desktop GUI), 'web' (browser GUI)",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=str(DEFAULT_PROCESS_DIR),
        help="Target folder or image file (defaults to './process'), or 'all' for strip",
    )
    parser.add_argument("--gui", action="store_true", help="Launch Desktop GUI (GTK3 with Web fallback)")
    parser.add_argument("--web", action="store_true", help="Launch local Web GUI in browser")
    parser.add_argument("--cli", action="store_true", help="Launch interactive terminal menu")
    parser.add_argument("--json", action="store_true", help="Output metadata report as JSON")
    parser.add_argument("--backup", action="store_true", help="Create a .bak backup before stripping/editing")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output file path for strip/edit")
    parser.add_argument("--c2pa-only", action="store_true", help="Only remove C2PA / AI Content Credentials segment")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="SECTION.KEY=VALUE",
        help="Set a metadata tag (e.g., --set exif.Artist='Theresa' --set iptc.SpecialInstructions='')",
    )
    parser.add_argument(
        "--clear",
        action="append",
        default=[],
        metavar="SECTION.KEY",
        help="Clear/delete a specific metadata tag (e.g., --clear iptc.SpecialInstructions)",
    )
    parser.add_argument(
        "--remove-c2pa",
        action="store_true",
        help="Remove C2PA / AI Content Credentials segment during edit",
    )

    args = parser.parse_args()

    target_path = Path(args.target)
    if not target_path.exists() and (DEFAULT_PROCESS_DIR / args.target).exists():
        target_path = DEFAULT_PROCESS_DIR / args.target

    # Handle --gui / --web flags
    if args.gui or args.command == "gui":
        folder = target_path if target_path.is_dir() else DEFAULT_PROCESS_DIR
        if not run_gtk_gui(folder):
            run_web_gui(folder)
        return

    if args.web or args.command == "web":
        folder = target_path if target_path.is_dir() else DEFAULT_PROCESS_DIR
        run_web_gui(folder)
        return

    if args.cli or args.command == "menu":
        folder = target_path if target_path.is_dir() else DEFAULT_PROCESS_DIR
        run_interactive_cli(folder)
        return

    if args.command == "strip":
        if args.target.lower() == "all" or target_path.is_dir():
            folder = DEFAULT_PROCESS_DIR if args.target.lower() == "all" else target_path
            files = list_images_in_folder(folder)
            if not files:
                print(f"[!] No images found in {folder}")
                sys.exit(1)
            for f in files:
                if args.backup:
                    shutil.copy2(f, f.with_suffix(f.suffix + ".bak"))
                if args.c2pa_only:
                    res = strip_all_metadata(
                        f,
                        remove_c2pa=True,
                        remove_exif=False,
                        remove_xmp=False,
                        remove_iptc=False,
                        remove_com=False,
                        remove_other_app=False,
                    )
                else:
                    res = strip_all_metadata(f)
                print(
                    f"[+] {f.name}: removed {len(res['removed_segments'])} segment(s) "
                    f"({res['bytes_saved']:,} bytes saved) -> {', '.join(res['removed_segments']) or 'already clean'}"
                )
        else:
            if not target_path.is_file():
                print(f"[!] File not found: {args.target}")
                sys.exit(1)
            if args.backup:
                shutil.copy2(target_path, target_path.with_suffix(target_path.suffix + ".bak"))
            out_p = Path(args.output) if args.output else None
            if args.c2pa_only:
                res = strip_all_metadata(
                    target_path,
                    output_path=out_p,
                    remove_c2pa=True,
                    remove_exif=False,
                    remove_xmp=False,
                    remove_iptc=False,
                    remove_com=False,
                    remove_other_app=False,
                )
            else:
                res = strip_all_metadata(target_path, output_path=out_p)
            print(
                f"[+] {target_path.name}: removed {len(res['removed_segments'])} segment(s) "
                f"({res['bytes_saved']:,} bytes saved) -> {', '.join(res['removed_segments']) or 'already clean'}"
            )
        return

    if args.command == "edit":
        if not target_path.is_file():
            print(f"[!] Please specify an image file to edit (e.g., python3 app.py edit process/{list_images_in_folder(DEFAULT_PROCESS_DIR)[0].name})")
            sys.exit(1)
        if args.backup:
            shutil.copy2(target_path, target_path.with_suffix(target_path.suffix + ".bak"))
        if args.set or args.clear or args.remove_c2pa:
            out_p = Path(args.output) if args.output else None
            apply_cli_edits(target_path, args.set, args.clear, args.remove_c2pa, out_p)
        else:
            interactive_edit_image(target_path)
        return

    # Default / 'show' command: Display all metadata for target (default: ./process)
    files = list_images_in_folder(target_path)
    if not files:
        print(f"[!] No images found at {target_path}")
        sys.exit(1)

    reports = [analyze_image(f) for f in files]
    if args.json:
        clean_reports = [{k: v for k, v in r.items() if not k.startswith("_")} for r in reports]
        print(json.dumps(clean_reports, indent=2, ensure_ascii=False))
        return

    render_summary_table(reports)
    for r in reports:
        render_image_full_report(r)

    # If invoked with no subcommand in an interactive terminal, offer the interactive menu
    if args.command is None and sys.stdin.isatty():
        print("\nTip: Run 'python3 app.py menu' for interactive mode, or 'python3 app.py --gui' for the GUI.")
        try:
            ans = input("Would you like to open the interactive Edit / Strip / GUI menu now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans == "y":
            run_interactive_cli(target_path if target_path.is_dir() else DEFAULT_PROCESS_DIR)


if __name__ == "__main__":
    main()

