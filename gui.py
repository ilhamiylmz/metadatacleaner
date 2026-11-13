#!/usr/bin/env python3
"""
gui.py - Native GTK 3.0 Desktop GUI (and built-in Web GUI fallback) for the
Image Metadata & AI Tag Inspector, Editor, and Remover.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
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


# ============================================================================
# Native GTK 3.0 Desktop GUI
# ============================================================================

def run_gtk_gui(initial_folder: Path) -> bool:
    """
    Launch the native GTK 3.0 desktop application.
    Returns True if GUI ran, or False if GTK/display could not be initialized.
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
    except Exception as exc:
        print(f"[!] GTK3 not available ({exc}).")
        return False

    ok, _ = Gtk.init_check()
    if not ok:
        return False

    # Custom CSS for clean modern badges and cards
    css = b"""
    .verdict-ai {
        background-color: #fde8e8;
        color: #9b1c1c;
        border: 1px solid #f8b4b4;
        border-radius: 6px;
        padding: 10px;
    }
    .verdict-social {
        background-color: #fef3c7;
        color: #92400e;
        border: 1px solid #fcd34d;
        border-radius: 6px;
        padding: 10px;
    }
    .verdict-clean {
        background-color: #def7ec;
        color: #03543f;
        border: 1px solid #84e1bc;
        border-radius: 6px;
        padding: 10px;
    }
    .badge-pill {
        background-color: #374151;
        color: #ffffff;
        border-radius: 4px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: bold;
    }
    """
    style_provider = Gtk.CssProvider()
    style_provider.load_from_data(css)
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(
            screen, style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    class MetadataStudioWindow(Gtk.Window):
        def __init__(self, folder: Path) -> None:
            super().__init__(title="Image Metadata & AI Tag Inspector / Editor")
            self.set_default_size(1180, 780)
            self.set_border_width(8)
            self.current_folder: Path = folder.resolve()
            self.current_image: Optional[Path] = None
            self.current_report: Optional[Dict[str, Any]] = None

            self._build_ui()
            self.refresh_file_list()

        def _build_ui(self) -> None:
            main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.add(main_vbox)

            # Top toolbar: Folder path, Browse, Refresh, Strip All
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            main_vbox.pack_start(toolbar, False, False, 0)

            lbl_folder = Gtk.Label(label="Folder:")
            toolbar.pack_start(lbl_folder, False, False, 0)

            self.entry_folder = Gtk.Entry()
            self.entry_folder.set_text(str(self.current_folder))
            self.entry_folder.connect("activate", lambda *_: self._on_folder_entry_activate())
            toolbar.pack_start(self.entry_folder, True, True, 0)

            btn_browse = Gtk.Button(label="Browse Folder...")
            btn_browse.connect("clicked", self._on_browse_folder)
            toolbar.pack_start(btn_browse, False, False, 0)

            btn_refresh = Gtk.Button(label="Refresh")
            btn_refresh.connect("clicked", lambda *_: self.refresh_file_list())
            toolbar.pack_start(btn_refresh, False, False, 0)

            btn_strip_all = Gtk.Button(label="Strip All Metadata in Folder...")
            btn_strip_all.connect("clicked", self._on_strip_all_in_folder)
            toolbar.pack_start(btn_strip_all, False, False, 0)

            # Horizontal Paned: Left = Image List, Right = Inspector & Actions
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            paned.set_position(360)
            main_vbox.pack_start(paned, True, True, 0)

            # Left: TreeView of images in folder
            left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            # Columns: Path(str), Filename(str), Dimensions(str), Size(str), Status(str)
            self.file_store = Gtk.ListStore(str, str, str, str, str)
            self.tree_files = Gtk.TreeView(model=self.file_store)
            self.tree_files.set_headers_visible(True)

            cols = [
                ("Image File", 1, 165),
                ("Dimensions", 2, 85),
                ("Status / AI Tag", 4, 110),
            ]
            for title, col_idx, min_w in cols:
                renderer = Gtk.CellRendererText()
                renderer.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE if col_idx == 1 else Pango.EllipsizeMode.END)
                col = Gtk.TreeViewColumn(title, renderer, text=col_idx)
                col.set_resizable(True)
                col.set_min_width(min_w)
                self.tree_files.append_column(col)

            sel = self.tree_files.get_selection()
            sel.connect("changed", self._on_file_selected)

            scroll_left = Gtk.ScrolledWindow()
            scroll_left.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll_left.add(self.tree_files)
            left_box.pack_start(scroll_left, True, True, 0)
            paned.pack1(left_box, resize=False, shrink=False)

            # Right: Selected image header + preview + action buttons + metadata tabs
            right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            right_box.set_margin_start(8)
            paned.pack2(right_box, resize=True, shrink=False)

            # Top card: Thumbnail + Verdict + Action Buttons
            top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            right_box.pack_start(top_hbox, False, False, 0)

            self.img_preview = Gtk.Image()
            self.img_preview.set_size_request(180, 120)
            preview_frame = Gtk.Frame()
            preview_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
            preview_frame.add(self.img_preview)
            top_hbox.pack_start(preview_frame, False, False, 0)

            info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            top_hbox.pack_start(info_vbox, True, True, 0)

            self.lbl_filename = Gtk.Label(xalign=0)
            self.lbl_filename.set_markup("<b>Select an image from the list</b>")
            self.lbl_filename.set_Selectable(True) if hasattr(self.lbl_filename, "set_Selectable") else self.lbl_filename.set_selectable(True)
            info_vbox.pack_start(self.lbl_filename, False, False, 0)

            self.lbl_tech = Gtk.Label(xalign=0)
            self.lbl_tech.set_selectable(True)
            info_vbox.pack_start(self.lbl_tech, False, False, 0)

            self.verdict_box = Gtk.EventBox()
            self.lbl_verdict = Gtk.Label(xalign=0)
            self.lbl_verdict.set_line_wrap(True)
            self.lbl_verdict.set_selectable(True)
            self.verdict_box.add(self.lbl_verdict)
            info_vbox.pack_start(self.verdict_box, False, False, 0)

            # Action buttons row
            btn_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            info_vbox.pack_start(btn_hbox, False, False, 2)

            self.btn_edit = Gtk.Button(label="Edit Metadata...")
            self.btn_edit.connect("clicked", self._on_edit_metadata)
            self.btn_edit.set_sensitive(False)
            btn_hbox.pack_start(self.btn_edit, False, False, 0)

            self.btn_strip_one = Gtk.Button(label="Remove All Metadata...")
            self.btn_strip_one.connect("clicked", self._on_strip_selected)
            self.btn_strip_one.set_sensitive(False)
            btn_hbox.pack_start(self.btn_strip_one, False, False, 0)

            self.btn_export_json = Gtk.Button(label="Export Metadata JSON...")
            self.btn_export_json.connect("clicked", self._on_export_json)
            self.btn_export_json.set_sensitive(False)
            btn_hbox.pack_start(self.btn_export_json, False, False, 0)

            # Notebook tabs for all metadata details
            self.notebook = Gtk.Notebook()
            right_box.pack_start(self.notebook, True, True, 0)

            self.txt_overview = self._create_text_tab("Overview & AI Tags")
            self.txt_c2pa = self._create_text_tab("C2PA / AI Provenance")
            self.txt_standard = self._create_text_tab("EXIF / IPTC / XMP / Comments")
            self.txt_segments = self._create_text_tab("Binary Segments & Hashes")

            # Statusbar
            self.lbl_status = Gtk.Label(xalign=0)
            self.lbl_status.set_text("Ready.")
            main_vbox.pack_start(self.lbl_status, False, False, 2)

        def _create_text_tab(self, title: str) -> Gtk.TextView:
            tv = Gtk.TextView()
            tv.set_editable(False)
            tv.set_cursor_visible(False)
            tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            tv.set_left_margin(10)
            tv.set_right_margin(10)
            tv.set_top_margin(8)
            tv.set_bottom_margin(8)
            tv.set_monospace(True)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            sw.add(tv)
            self.notebook.append_page(sw, Gtk.Label(label=title))
            return tv

        def _set_textview(self, tv: Gtk.TextView, text: str) -> None:
            buf = tv.get_buffer()
            buf.set_text(text)

        def _on_folder_entry_activate(self) -> None:
            p = Path(self.entry_folder.get_text().strip())
            if p.exists():
                self.current_folder = p.resolve()
                self.refresh_file_list()

        def _on_browse_folder(self, _btn: Gtk.Widget) -> None:
            dlg = Gtk.FileChooserDialog(
                title="Select Image Folder",
                parent=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
            )
            dlg.set_current_folder(str(self.current_folder))
            if dlg.run() == Gtk.ResponseType.OK:
                chosen = dlg.get_filename()
                if chosen:
                    self.current_folder = Path(chosen).resolve()
                    self.entry_folder.set_text(str(self.current_folder))
                    self.refresh_file_list()
            dlg.destroy()

        def refresh_file_list(self) -> None:
            prev_selected = str(self.current_image) if self.current_image else None
            self.file_store.clear()
            files = list_images_in_folder(self.current_folder)
            select_iter = None

            for f in files:
                try:
                    rep = analyze_image(f)
                    fi = rep["file_info"]
                    ai = rep["ai_summary"]
                    if ai["is_ai_generated"]:
                        status = "AI / C2PA"
                    elif ai["has_social_tracking"]:
                        status = "IPTC / FBMD"
                    elif ai["has_any_metadata"]:
                        status = "Has Metadata"
                    else:
                        status = "Clean"
                    it = self.file_store.append([
                        str(f),
                        fi["filename"],
                        fi["dimensions"].replace(" px", ""),
                        fi["size_human"].split(" (")[0],
                        status,
                    ])
                    if prev_selected and str(f) == prev_selected:
                        select_iter = it
                except Exception as exc:
                    self.file_store.append([str(f), f.name, "?", "?", f"Error: {exc}"])

            if select_iter is not None:
                self.tree_files.get_selection().select_iter(select_iter)
            elif len(self.file_store) > 0:
                self.tree_files.get_selection().select_path(Gtk.TreePath.new_first())
            else:
                self.current_image = None
                self.current_report = None
                self.btn_edit.set_sensitive(False)
                self.btn_strip_one.set_sensitive(False)
                self.btn_export_json.set_sensitive(False)
                self.lbl_status.set_text(f"No images found in {self.current_folder}")

            self.lbl_status.set_text(f"Loaded {len(files)} image(s) from {self.current_folder}")

        def _on_file_selected(self, selection: Gtk.TreeSelection) -> None:
            model, treeiter = selection.get_selected()
            if treeiter is None:
                return
            path_str = model[treeiter][0]
            self.load_image_details(Path(path_str))

        def load_image_details(self, img_path: Path) -> None:
            self.current_image = img_path
            try:
                rep = analyze_image(img_path)
            except Exception as exc:
                self.lbl_status.set_text(f"Error analyzing {img_path.name}: {exc}")
                return
            self.current_report = rep
            self.btn_edit.set_sensitive(True)
            self.btn_strip_one.set_sensitive(True)
            self.btn_export_json.set_sensitive(True)

            fi = rep["file_info"]
            ai = rep["ai_summary"]

            # Load thumbnail
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(img_path), 180, 120, True)
                self.img_preview.set_from_pixbuf(pb)
            except Exception:
                self.img_preview.clear()

            self.lbl_filename.set_markup(
                f"<span size='large' weight='bold'>{html.escape(fi['filename'])}</span>"
            )
            self.lbl_tech.set_markup(
                f"<b>Format:</b> {html.escape(fi['format'])} ({html.escape(fi['encoding_mode'])})   "
                f"<b>Dimensions:</b> {html.escape(fi['dimensions'])}   "
                f"<b>Size:</b> {html.escape(fi['size_human'])}"
            )

            badges_str = "  |  ".join(ai["badges"]) if ai["badges"] else "No AI / Tracking Badges"
            self.lbl_verdict.set_markup(
                f"<b>Verdict:</b> {html.escape(ai['verdict'])}\n"
                f"<small><b>Detected Tags:</b> {html.escape(badges_str)}</small>"
            )

            ctx = self.verdict_box.get_style_context()
            for cls in ("verdict-ai", "verdict-social", "verdict-clean"):
                ctx.remove_class(cls)
            if ai["is_ai_generated"]:
                ctx.add_class("verdict-ai")
            elif ai["has_social_tracking"] or ai["has_any_metadata"]:
                ctx.add_class("verdict-social")
            else:
                ctx.add_class("verdict-clean")

            # Populate Tabs
            self._set_textview(self.txt_overview, format_overview_text(rep))
            self._set_textview(self.txt_c2pa, format_c2pa_text(rep))
            self._set_textview(self.txt_standard, format_standard_meta_text(rep))
            self._set_textview(self.txt_segments, format_segments_text(rep))

        def _on_strip_selected(self, _btn: Gtk.Widget) -> None:
            if not self.current_image:
                return
            dlg = Gtk.Dialog(
                title=f"Remove Metadata - {self.current_image.name}",
                transient_for=self,
                flags=0,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                "Remove Metadata", Gtk.ResponseType.OK,
            )
            dlg.set_default_size(460, 280)
            box = dlg.get_content_area()
            box.set_spacing(8)
            box.set_border_width(12)

            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(
                f"<b>Select which metadata categories to remove from:</b>\n"
                f"<tt>{html.escape(self.current_image.name)}</tt>"
            )
            box.pack_start(lbl, False, False, 0)

            chk_c2pa = Gtk.CheckButton(label="C2PA / JUMBF AI Content Credentials & SynthID Provenance (APP11)")
            chk_c2pa.set_active(True)
            chk_iptc = Gtk.CheckButton(label="IPTC / Photoshop 8BIM & Social Tracking like FBMD (APP13)")
            chk_iptc.set_active(True)
            chk_exif = Gtk.CheckButton(label="EXIF Camera / Software / Artist / GPS Tags (APP1)")
            chk_exif.set_active(True)
            chk_xmp = Gtk.CheckButton(label="XMP RDF/XML Packet (APP1)")
            chk_xmp.set_active(True)
            chk_com = Gtk.CheckButton(label="JPEG / PNG Text Comments (COM / tEXt / iTXt)")
            chk_com.set_active(True)
            chk_other = Gtk.CheckButton(label="Other APP Segments (ICC Profile, Adobe APP14, etc.)")
            chk_other.set_active(True)

            for w in (chk_c2pa, chk_iptc, chk_exif, chk_xmp, chk_com, chk_other):
                box.pack_start(w, False, False, 0)

            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            box.pack_start(sep, False, False, 4)

            chk_backup = Gtk.CheckButton(label="Save a backup copy (.bak) before modifying")
            chk_backup.set_active(False)
            box.pack_start(chk_backup, False, False, 0)

            dlg.show_all()
            resp = dlg.run()
            if resp == Gtk.ResponseType.OK:
                if chk_backup.get_active():
                    bak = self.current_image.with_suffix(self.current_image.suffix + ".bak")
                    shutil.copy2(self.current_image, bak)
                res = strip_all_metadata(
                    self.current_image,
                    remove_c2pa=chk_c2pa.get_active(),
                    remove_iptc=chk_iptc.get_active(),
                    remove_exif=chk_exif.get_active(),
                    remove_xmp=chk_xmp.get_active(),
                    remove_com=chk_com.get_active(),
                    remove_other_app=chk_other.get_active(),
                )
                removed_cnt = len(res["removed_segments"])
                self.lbl_status.set_text(
                    f"Removed {removed_cnt} segment(s) ({res['bytes_saved']:,} bytes saved) from {self.current_image.name}"
                )
                self.refresh_file_list()
            dlg.destroy()

        def _on_strip_all_in_folder(self, _btn: Gtk.Widget) -> None:
            files = list_images_in_folder(self.current_folder)
            if not files:
                return
            dlg = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text=f"Remove ALL metadata (including AI/C2PA & IPTC tags) from all {len(files)} images in '{self.current_folder.name}'?",
            )
            dlg.format_secondary_text(
                "This losslessly strips EXIF, XMP, IPTC/8BIM (FBMD), C2PA/JUMBF AI credentials, and comments without recompressing image pixels."
            )
            resp = dlg.run()
            dlg.destroy()
            if resp == Gtk.ResponseType.YES:
                total_saved = 0
                for f in files:
                    r = strip_all_metadata(f)
                    total_saved += r["bytes_saved"]
                self.refresh_file_list()
                self.lbl_status.set_text(
                    f"Stripped metadata from {len(files)} images ({total_saved:,} bytes removed total)."
                )

        def _on_edit_metadata(self, _btn: Gtk.Widget) -> None:
            if not self.current_image or not self.current_report:
                return
            editable = get_editable_fields(self.current_report)

            dlg = Gtk.Dialog(
                title=f"Edit Metadata - {self.current_image.name}",
                transient_for=self,
                flags=0,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                "Save Metadata Changes", Gtk.ResponseType.OK,
            )
            dlg.set_default_size(740, 560)
            content = dlg.get_content_area()
            content.set_spacing(8)
            content.set_border_width(10)

            info_lbl = Gtk.Label(xalign=0)
            info_lbl.set_markup(
                f"<b>Editing metadata for:</b> <tt>{html.escape(self.current_image.name)}</tt>\n"
                "<small>Modify existing fields, add new tags, or clear a field's value to remove it.</small>"
            )
            content.pack_start(info_lbl, False, False, 0)

            nb = Gtk.Notebook()
            content.pack_start(nb, True, True, 0)

            entries: Dict[str, Dict[str, Gtk.Entry]] = {
                "exif": {},
                "iptc": {},
                "xmp": {},
                "comment": {},
                "c2pa": {},
            }

            def make_form_tab(cat: str, tab_title: str, field_dict: Dict[str, str], helper_note: str = "") -> None:
                vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                vbox.set_border_width(10)
                if helper_note:
                    hlbl = Gtk.Label(xalign=0)
                    hlbl.set_markup(f"<small>{helper_note}</small>")
                    hlbl.set_line_wrap(True)
                    vbox.pack_start(hlbl, False, False, 2)

                grid = Gtk.Grid()
                grid.set_column_spacing(12)
                grid.set_row_spacing(6)
                vbox.pack_start(grid, False, False, 0)

                for row_idx, (k, v) in enumerate(field_dict.items()):
                    klbl = Gtk.Label(label=f"{k}:", xalign=0)
                    ent = Gtk.Entry()
                    ent.set_hexpand(True)
                    ent.set_text(str(v))
                    btn_clr = Gtk.Button(label="Clear")
                    btn_clr.connect("clicked", lambda _b, e=ent: e.set_text(""))
                    grid.attach(klbl, 0, row_idx, 1, 1)
                    grid.attach(ent, 1, row_idx, 1, 1)
                    grid.attach(btn_clr, 2, row_idx, 1, 1)
                    entries[cat][k] = ent

                sw = Gtk.ScrolledWindow()
                sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                sw.add(vbox)
                nb.append_page(sw, Gtk.Label(label=tab_title))

            make_form_tab(
                "iptc",
                "IPTC / Photoshop (APP13)",
                editable["iptc"],
                "Includes ObjectName (Title), Byline (Author), Keywords (comma-separated), and SpecialInstructions (e.g., Facebook/Instagram FBMD tag).",
            )
            make_form_tab(
                "exif",
                "EXIF Tags (APP1)",
                editable["exif"],
                "Standard EXIF IFD0 & ExifIFD metadata tags.",
            )
            make_form_tab(
                "xmp",
                "XMP & Comment",
                {**editable["xmp"], **editable["comment"]},
                "Adobe XMP Dublin Core / CreatorTool tags and JPEG/PNG Comment.",
            )

            chk_remove_c2pa = Gtk.CheckButton(
                label="Remove entire C2PA / JUMBF AI Content Credentials segment (APP11) on save"
            )
            if editable["c2pa"]:
                make_form_tab(
                    "c2pa",
                    "C2PA / AI Tags (APP11)",
                    editable["c2pa"],
                    "Edit C2PA Claim Generator and AI Action descriptions/digitalSourceType directly in the JUMBF CBOR manifest, or check the box below to remove C2PA completely.",
                )
                content.pack_start(chk_remove_c2pa, False, False, 2)

            dlg.show_all()
            resp = dlg.run()
            if resp == Gtk.ResponseType.OK:
                new_edits: Dict[str, Dict[str, Any]] = {
                    "exif": {k: e.get_text().strip() for k, e in entries["exif"].items()},
                    "iptc": {k: e.get_text().strip() for k, e in entries["iptc"].items()},
                    "xmp": {},
                    "comment": {},
                    "c2pa": {k: e.get_text().strip() for k, e in entries["c2pa"].items()},
                }
                for k, e in entries["xmp"].items():
                    if k == "Comment":
                        new_edits["comment"]["Comment"] = e.get_text().strip()
                    else:
                        new_edits["xmp"][k] = e.get_text().strip()

                update_image_metadata(
                    self.current_image,
                    new_edits,
                    remove_c2pa=chk_remove_c2pa.get_active(),
                )
                self.lbl_status.set_text(f"Updated metadata for {self.current_image.name}")
                self.refresh_file_list()
            dlg.destroy()

        def _on_export_json(self, _btn: Gtk.Widget) -> None:
            if not self.current_image or not self.current_report:
                return
            dlg = Gtk.FileChooserDialog(
                title="Export Metadata Report as JSON",
                parent=self,
                action=Gtk.FileChooserAction.SAVE,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
            )
            dlg.set_current_name(f"{self.current_image.stem}_metadata.json")
            if dlg.run() == Gtk.ResponseType.OK:
                out_file = dlg.get_filename()
                if out_file:
                    export_data = {k: v for k, v in self.current_report.items() if not k.startswith("_")}
                    Path(out_file).write_text(json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    self.lbl_status.set_text(f"Exported JSON metadata to {out_file}")
            dlg.destroy()

    win = MetadataStudioWindow(initial_folder)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return True


# ============================================================================
# Text Formatting Helpers for GUI Tabs
# ============================================================================

def format_overview_text(rep: Dict[str, Any]) -> str:
    fi = rep["file_info"]
    ai = rep["ai_summary"]
    lines = [
        "========================================================================",
        f" FILE & IMAGE SUMMARY: {fi['filename']}",
        "========================================================================",
        f"  Full Path          : {fi['path']}",
        f"  File Size          : {fi['size_human']}",
        f"  SHA-256            : {fi['sha256']}",
        f"  Last Modified      : {fi['modified_time']}",
        f"  Format             : {fi['format']}",
        f"  Dimensions         : {fi['dimensions']}",
        f"  Encoding Mode      : {fi.get('encoding_mode', 'N/A')}",
        f"  Bit Depth / Chans  : {fi.get('bit_depth', 0)}-bit / {fi.get('channels', 0)} channels",
        f"  Chroma Subsampling : {fi.get('subsampling') or 'N/A'}",
        "",
        "========================================================================",
        " AI & PROVENANCE DETECTION SUMMARY",
        "========================================================================",
        f"  Verdict            : {ai['verdict']}",
        f"  AI-Generated Flag  : {'YES' if ai['is_ai_generated'] else 'No'}",
        f"  C2PA Credentials   : {'YES (Present)' if ai['has_c2pa'] else 'No'}",
        f"  SynthID Watermark  : {'YES (Claimed in C2PA)' if ai['has_synthid'] else 'No'}",
        f"  Social Media Tag   : {'YES (IPTC FBMD)' if ai['has_social_tracking'] else 'No'}",
        f"  Badges             : {', '.join(ai['badges']) if ai['badges'] else 'None'}",
    ]
    if ai["details"]:
        lines.append("\n  Extracted AI / Tracking Details:")
        for k, v in ai["details"].items():
            if isinstance(v, list):
                lines.append(f"    * {k}:")
                for item in v:
                    lines.append(f"        - {item}")
            elif isinstance(v, dict):
                lines.append(f"    * {k}:")
                for sk, sv in v.items():
                    lines.append(f"        - {sk}: {sv}")
            else:
                lines.append(f"    * {k}: {v}")

    # Also summarize currently populated standard fields
    ed = get_editable_fields(rep)
    lines.append("\n========================================================================")
    lines.append(" POPULATED METADATA FIELDS AT A GLANCE")
    lines.append("========================================================================")
    any_pop = False
    for cat in ("exif", "iptc", "xmp", "comment", "c2pa"):
        pop = {k: v for k, v in ed.get(cat, {}).items() if v}
        if pop:
            any_pop = True
            lines.append(f"\n  [{cat.upper()}]")
            for k, v in pop.items():
                lines.append(f"    {k:<38} : {v}")
    if not any_pop:
        lines.append("  (No editable EXIF, IPTC, XMP, Comment, or C2PA fields populated)")

    return "\n".join(lines)


def format_c2pa_text(rep: Dict[str, Any]) -> str:
    c2pa = rep.get("c2pa", {})
    if not c2pa.get("present"):
        return "No C2PA / JUMBF Content Credentials (APP11 / caBX) present in this image."

    lines = [
        "========================================================================",
        " C2PA / JUMBF CONTENT CREDENTIALS & AI PROVENANCE CHAIN",
        "========================================================================",
        f"  Active Manifest ID : {c2pa.get('active_manifest_id')}",
        f"  Total Manifests    : {len(c2pa.get('manifests', []))}",
        f"  Claim Generators   : {', '.join(c2pa.get('claim_generators', []))}",
        f"  Digital Source     : {', '.join(c2pa.get('digital_source_types', []))}",
        f"  SynthID Detected   : {'YES' if c2pa.get('synthid_detected') else 'No'}",
        "",
    ]

    for idx, m in enumerate(c2pa.get("manifests", []), 1):
        role = "ACTIVE MANIFEST" if m.get("is_active_manifest") else f"INGREDIENT MANIFEST #{idx}"
        lines.append(f"------------------------------------------------------------------------")
        lines.append(f" Manifest #{idx} [{role}]: {m.get('manifest_id')}")
        lines.append(f"------------------------------------------------------------------------")
        claim = m.get("claim", {})
        cgi = claim.get("claim_generator_info", {})
        if isinstance(cgi, dict):
            lines.append(f"  Claim Generator : {cgi.get('name')} (version: {cgi.get('version')})")
        lines.append(f"  Instance ID     : {claim.get('instanceID')}")
        lines.append(f"  Hash Algorithm  : {claim.get('alg')}")

        if m.get("actions"):
            lines.append("\n  Actions (`c2pa.actions.v2`):")
            for a in m["actions"]:
                lines.append(f"    - Action            : {a.get('action')}")
                if a.get("description"):
                    lines.append(f"      Description       : {a.get('description')}")
                if a.get("digitalSourceType"):
                    lines.append(f"      DigitalSourceType : {a.get('digitalSourceTypeLabel')}")
                    lines.append(f"      Source URI        : {a.get('digitalSourceType')}")

        if m.get("ingredients"):
            lines.append("\n  Ingredients (`c2pa.ingredient.v3`):")
            for ing in m["ingredients"]:
                lines.append(
                    f"    - {ing.get('label')}: relationship={ing.get('relationship')}, "
                    f"desc={ing.get('description')!r}, format={ing.get('format') or 'N/A'}"
                )
                if "validation_codes" in ing:
                    lines.append(
                        f"      Validation Passed ({ing.get('validation_success_count')} checks): "
                        f"{', '.join(ing['validation_codes'])}"
                    )

        sig = m.get("signature", {})
        if sig:
            lines.append("\n  Cryptographic Signature (`c2pa.signature` COSE_Sign1):")
            lines.append(f"    Algorithm           : {sig.get('algorithm', 'Unknown')}")
            if sig.get("timestamp_utc"):
                lines.append(f"    Signing Timestamp   : {sig.get('timestamp_utc')}")
            if sig.get("timestamp_authority"):
                lines.append(f"    Timestamp Authority : {sig.get('timestamp_authority')}")
            for cert in sig.get("certificates", []):
                lines.append(f"    Certificate [{cert.get('role')}]:")
                lines.append(f"      Subject           : {cert.get('subject')}")
                lines.append(f"      Issuer            : {cert.get('issuer')}")
                lines.append(f"      Serial Number     : {cert.get('serial_number')}")
                lines.append(f"      Validity          : {cert.get('valid_from')}  -->  {cert.get('valid_until')}")
        lines.append("")

    return "\n".join(lines)


def format_standard_meta_text(rep: Dict[str, Any]) -> str:
    lines: List[str] = []

    # JFIF
    lines.append("========================================================================")
    lines.append(" JFIF HEADER (JPEG APP0)")
    lines.append("========================================================================")
    if rep.get("jfif"):
        for k, v in rep["jfif"].items():
            lines.append(f"  {k:<24} : {v}")
    else:
        lines.append("  (None)")

    # IPTC / Photoshop 8BIM
    lines.append("\n========================================================================")
    lines.append(" IPTC / PHOTOSHOP 8BIM (JPEG APP13)")
    lines.append("========================================================================")
    iptc_sec = rep.get("iptc", {})
    if iptc_sec.get("resources") or iptc_sec.get("iptc"):
        for r in iptc_sec.get("resources", []):
            lines.append(f"  8BIM Resource {r['id']} ({r['name']}) - {r['size']} bytes")
        for k, v in iptc_sec.get("iptc", {}).items():
            lines.append(f"  IPTC {k:<22} : {v}")
    else:
        lines.append("  (No IPTC / Photoshop 8BIM metadata present)")

    # EXIF
    lines.append("\n========================================================================")
    lines.append(" EXIF METADATA (APP1 / eXIf)")
    lines.append("========================================================================")
    exif_sec = rep.get("exif", {})
    if exif_sec.get("flat"):
        lines.append(f"  Byte Order               : {exif_sec.get('byte_order')}")
        for sub in ("IFD0", "ExifIFD", "GPSIFD", "IFD1"):
            sub_dict = exif_sec.get(sub, {})
            if sub_dict:
                lines.append(f"\n  [{sub}]")
                for k, v in sub_dict.items():
                    lines.append(f"    {k:<26} : {v}")
    else:
        lines.append("  (No EXIF metadata present)")

    # XMP
    lines.append("\n========================================================================")
    lines.append(" XMP METADATA (RDF/XML)")
    lines.append("========================================================================")
    xmp_sec = rep.get("xmp", {})
    if xmp_sec.get("fields") or xmp_sec.get("raw_xml"):
        for k, v in xmp_sec.get("fields", {}).items():
            lines.append(f"  {k:<32} : {v}")
        if xmp_sec.get("raw_xml"):
            lines.append("\n  Raw XMP Packet:")
            lines.append(xmp_sec["raw_xml"])
    else:
        lines.append("  (No XMP metadata present)")

    # Comments & PNG Chunks
    lines.append("\n========================================================================")
    lines.append(" COMMENTS & TEXT CHUNKS")
    lines.append("========================================================================")
    if rep.get("comments"):
        for i, c in enumerate(rep["comments"], 1):
            lines.append(f"  Comment #{i}: {c}")
    if rep.get("png_chunks"):
        for k, v in rep["png_chunks"].items():
            lines.append(f"  PNG {k}: {v}")
    if not rep.get("comments") and not rep.get("png_chunks"):
        lines.append("  (No comments or text chunks present)")

    return "\n".join(lines)


def format_segments_text(rep: Dict[str, Any]) -> str:
    fi = rep["file_info"]
    lines = [
        "========================================================================",
        " BINARY FILE STRUCTURE & SEGMENT MAP",
        "========================================================================",
        f"  File     : {fi['filename']}",
        f"  SHA-256  : {fi['sha256']}",
        f"  Size     : {fi['size_human']}",
        f"  DQT/DHT  : {fi.get('quantization_tables', 0)} Quantization Table(s), {fi.get('huffman_tables', 0)} Huffman Table(s)",
        "",
        f"  {'Offset':>10}  {'Length':>12}  {'Marker':<10}  {'Segment Description'}",
        f"  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*40}",
    ]
    for s in rep.get("segments", []):
        lines.append(
            f"  {s['offset']:10d}  {s['length']:10d} B  {s['marker']:<10}  {s['name']}"
        )
    return "\n".join(lines)


# ============================================================================
# Built-in Local Web GUI (Browser Interface / Fallback)
# ============================================================================

def run_web_gui(initial_folder: Path, port: int = 8765, open_browser: bool = True) -> None:
    """Run a local single-page Web GUI for viewing, editing, and stripping image metadata."""
    folder = initial_folder.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/":
                body = WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/list":
                imgs = list_images_in_folder(folder)
                items = []
                for f in imgs:
                    rep = analyze_image(f)
                    items.append({
                        "filename": f.name,
                        "dimensions": rep["file_info"]["dimensions"],
                        "size_human": rep["file_info"]["size_human"],
                        "verdict": rep["ai_summary"]["verdict"],
                        "is_ai": rep["ai_summary"]["is_ai_generated"],
                        "has_social": rep["ai_summary"]["has_social_tracking"],
                        "has_meta": rep["ai_summary"]["has_any_metadata"],
                        "badges": rep["ai_summary"]["badges"],
                    })
                self._send_json({"folder": str(folder), "images": items})
            elif parsed.path == "/api/detail":
                name = qs.get("name", [""])[0]
                target = (folder / Path(name).name).resolve()
                if not target.exists():
                    self._send_json({"error": "Not found"}, 404)
                    return
                rep = analyze_image(target)
                editable = get_editable_fields(rep)
                clean_rep = {k: v for k, v in rep.items() if not k.startswith("_")}
                self._send_json({
                    "report": clean_rep,
                    "editable": editable,
                    "overview_text": format_overview_text(rep),
                    "c2pa_text": format_c2pa_text(rep),
                    "standard_text": format_standard_meta_text(rep),
                    "segments_text": format_segments_text(rep),
                })
            elif parsed.path == "/api/image":
                name = qs.get("name", [""])[0]
                target = (folder / Path(name).name).resolve()
                if not target.exists():
                    self.send_error(404)
                    return
                raw = target.read_bytes()
                mime = "image/png" if target.suffix.lower() == ".png" else "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/api/strip":
                name = payload.get("name", "")
                if name == "__ALL__":
                    files = list_images_in_folder(folder)
                    saved = 0
                    for f in files:
                        r = strip_all_metadata(f)
                        saved += r["bytes_saved"]
                    self._send_json({"ok": True, "message": f"Stripped all metadata from {len(files)} images ({saved:,} bytes saved)."})
                else:
                    target = (folder / Path(name).name).resolve()
                    r = strip_all_metadata(target)
                    self._send_json({
                        "ok": True,
                        "message": f"Removed {len(r['removed_segments'])} metadata segment(s) ({r['bytes_saved']:,} bytes) from {target.name}.",
                    })
            elif self.path == "/api/edit":
                name = payload.get("name", "")
                edits = payload.get("edits", {})
                remove_c2pa = bool(payload.get("remove_c2pa", False))
                target = (folder / Path(name).name).resolve()
                update_image_metadata(target, edits, remove_c2pa=remove_c2pa)
                self._send_json({"ok": True, "message": f"Saved metadata edits to {target.name}."})
            else:
                self.send_error(404)

    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n[*] Web GUI running at: {url}")
    print("    Press Ctrl+C to stop the server.\n")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down Web GUI.")
        server.server_close()


WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Image Metadata & AI Tag Studio</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; }
  header { background: #1e293b; padding: 14px 22px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }
  header h1 { margin: 0; font-size: 18px; color: #f8fafc; }
  .layout { display: grid; grid-template-columns: 340px 1fr; height: calc(100vh - 57px); }
  .sidebar { background: #1e293b; border-right: 1px solid #334155; overflow-y: auto; padding: 12px; }
  .file-item { padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 8px; border: 1px solid #334155; background: #0f172a; }
  .file-item.active { border-color: #38bdf8; background: #1e293b; }
  .file-name { font-weight: 600; font-size: 13px; word-break: break-all; }
  .file-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-top: 6px; margin-right: 4px; }
  .badge-ai { background: #7f1d1d; color: #fecaca; }
  .badge-social { background: #78350f; color: #fde68a; }
  .badge-clean { background: #064e3b; color: #a7f3d0; }
  .main { padding: 18px; overflow-y: auto; }
  .top-card { display: flex; gap: 18px; background: #1e293b; padding: 16px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 16px; align-items: center; }
  .top-card img { width: 180px; height: 120px; object-fit: contain; background: #0f172a; border-radius: 6px; border: 1px solid #334155; }
  .verdict-banner { padding: 10px 12px; border-radius: 6px; font-weight: 600; margin: 8px 0; font-size: 13px; }
  .btn { background: #2563eb; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; margin-right: 8px; }
  .btn-danger { background: #dc2626; }
  .btn-secondary { background: #475569; }
  .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
  .tab-btn { background: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: 600; }
  .tab-btn.active { background: #38bdf8; color: #0f172a; border-color: #38bdf8; }
  pre { background: #1e293b; padding: 16px; border-radius: 8px; border: 1px solid #334155; overflow-x: auto; font-size: 12.5px; line-height: 1.5; color: #f1f5f9; }
  .form-sec { background: #1e293b; padding: 16px; border-radius: 8px; border: 1px solid #334155; margin-bottom: 14px; }
  .form-row { display: grid; grid-template-columns: 280px 1fr 70px; gap: 8px; margin-bottom: 8px; align-items: center; }
  .form-row input { padding: 7px 10px; border-radius: 5px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; }
  #toast { position: fixed; bottom: 18px; right: 18px; background: #059669; color: white; padding: 10px 18px; border-radius: 8px; display: none; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Image Metadata & AI Tag Inspector / Editor</h1>
  <div>
    <button class="btn btn-danger" onclick="stripAll()">Strip All Metadata in Folder</button>
    <button class="btn btn-secondary" onclick="loadList()">Refresh</button>
  </div>
</header>
<div class="layout">
  <div class="sidebar" id="fileList"></div>
  <div class="main" id="mainPane">Select an image from the left sidebar.</div>
</div>
<div id="toast"></div>
<script>
let currentFile = null;
let currentData = null;
let currentTab = 'overview';

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3500);
}

async function loadList() {
  const res = await fetch('/api/list');
  const data = await res.json();
  const el = document.getElementById('fileList');
  el.innerHTML = '';
  data.images.forEach((img, idx) => {
    const div = document.createElement('div');
    div.className = 'file-item' + (currentFile === img.filename ? ' active' : '');
    let bcls = img.is_ai ? 'badge-ai' : (img.has_social || img.has_meta ? 'badge-social' : 'badge-clean');
    let btxt = img.is_ai ? 'AI / C2PA' : (img.has_social ? 'IPTC / FBMD' : (img.has_meta ? 'Metadata' : 'Clean'));
    div.innerHTML = `
      <div class="file-name">${img.filename}</div>
      <div class="file-sub">${img.dimensions} &bull; ${img.size_human.split(' (')[0]}</div>
      <span class="badge ${bcls}">${btxt}</span>
    `;
    div.onclick = () => selectFile(img.filename);
    el.appendChild(div);
    if (!currentFile && idx === 0) selectFile(img.filename);
  });
}

async function selectFile(name) {
  currentFile = name;
  const res = await fetch('/api/detail?name=' + encodeURIComponent(name));
  currentData = await res.json();
  renderMain();
  loadList();
}

function setTab(tab) {
  currentTab = tab;
  renderMain();
}

function renderMain() {
  if (!currentData) return;
  const rep = currentData.report;
  const fi = rep.file_info;
  const ai = rep.ai_summary;
  const bcls = ai.is_ai_generated ? 'badge-ai' : (ai.has_social_tracking || ai.has_any_metadata ? 'badge-social' : 'badge-clean');

  let tabContent = '';
  if (currentTab === 'overview') tabContent = `<pre>${escapeHtml(currentData.overview_text)}</pre>`;
  else if (currentTab === 'c2pa') tabContent = `<pre>${escapeHtml(currentData.c2pa_text)}</pre>`;
  else if (currentTab === 'standard') tabContent = `<pre>${escapeHtml(currentData.standard_text)}</pre>`;
  else if (currentTab === 'segments') tabContent = `<pre>${escapeHtml(currentData.segments_text)}</pre>`;
  else if (currentTab === 'edit') tabContent = renderEditForm(currentData.editable);

  document.getElementById('mainPane').innerHTML = `
    <div class="top-card">
      <img src="/api/image?name=${encodeURIComponent(fi.filename)}&t=${Date.now()}" alt="preview">
      <div style="flex:1">
        <div style="font-size:17px;font-weight:700">${escapeHtml(fi.filename)}</div>
        <div style="font-size:13px;color:#94a3b8;margin-top:4px">${fi.format} (${fi.encoding_mode}) &bull; ${fi.dimensions} &bull; ${fi.size_human}</div>
        <div class="verdict-banner ${bcls}">${escapeHtml(ai.verdict)}</div>
        <div>
          <button class="btn" onclick="setTab('edit')">Edit Metadata</button>
          <button class="btn btn-danger" onclick="stripSingle('${escapeHtml(fi.filename)}')">Remove All Metadata</button>
        </div>
      </div>
    </div>
    <div class="tabs">
      <button class="tab-btn ${currentTab==='overview'?'active':''}" onclick="setTab('overview')">Overview & AI Tags</button>
      <button class="tab-btn ${currentTab==='c2pa'?'active':''}" onclick="setTab('c2pa')">C2PA / AI Provenance</button>
      <button class="tab-btn ${currentTab==='standard'?'active':''}" onclick="setTab('standard')">EXIF / IPTC / XMP</button>
      <button class="tab-btn ${currentTab==='segments'?'active':''}" onclick="setTab('segments')">Binary Segments</button>
      <button class="tab-btn ${currentTab==='edit'?'active':''}" onclick="setTab('edit')">Edit Metadata</button>
    </div>
    ${tabContent}
  `;
}

function renderEditForm(ed) {
  let html = `<div class="form-sec"><h3>Edit Current & Standard Metadata Fields</h3>`;
  for (const [cat, fields] of Object.entries(ed)) {
    const entries = Object.entries(fields);
    if (!entries.length) continue;
    html += `<h4 style="color:#38bdf8;text-transform:uppercase;margin-top:16px">${cat}</h4>`;
    for (const [k, v] of entries) {
      const id = `ed_${cat}_${k}`;
      html += `
        <div class="form-row">
          <label style="font-size:12.5px">${escapeHtml(k)}</label>
          <input type="text" id="${escapeHtml(id)}" data-cat="${escapeHtml(cat)}" data-key="${escapeHtml(k)}" value="${escapeHtml(v)}">
          <button class="btn btn-secondary" onclick="document.getElementById('${escapeHtml(id)}').value=''">Clear</button>
        </div>`;
    }
  }
  html += `
    <div style="margin-top:14px">
      <label><input type="checkbox" id="chkRemoveC2pa"> Remove entire C2PA / AI Content Credentials segment (APP11) on save</label>
    </div>
    <div style="margin-top:14px">
      <button class="btn" onclick="saveEdits()">Save Metadata Changes</button>
    </div>
  </div>`;
  return html;
}

async function saveEdits() {
  const edits = {exif:{}, iptc:{}, xmp:{}, comment:{}, c2pa:{}};
  document.querySelectorAll('input[data-cat]').forEach(inp => {
    edits[inp.dataset.cat][inp.dataset.key] = inp.value;
  });
  const remove_c2pa = document.getElementById('chkRemoveC2pa')?.checked || false;
  const res = await fetch('/api/edit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: currentFile, edits, remove_c2pa})
  });
  const d = await res.json();
  showToast(d.message);
  currentTab = 'overview';
  await selectFile(currentFile);
}

async function stripSingle(name) {
  if (!confirm('Remove all metadata (including AI/C2PA and IPTC tags) from ' + name + '?')) return;
  const res = await fetch('/api/strip', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name})
  });
  const d = await res.json();
  showToast(d.message);
  await selectFile(name);
}

async function stripAll() {
  if (!confirm('Remove all metadata from ALL images in the process folder?')) return;
  const res = await fetch('/api/strip', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: '__ALL__'})
  });
  const d = await res.json();
  showToast(d.message);
  if (currentFile) await selectFile(currentFile);
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadList();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    default_folder = Path(__file__).resolve().parent / "process"
    if not run_gtk_gui(default_folder):
        run_web_gui(default_folder)

