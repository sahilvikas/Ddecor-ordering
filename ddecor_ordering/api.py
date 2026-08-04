"""
DDécor Ordering API v3
Returns order_result with per-item verification status, portal PO numbers, ERPNext PO links.
"""

import json
import frappe
from frappe import _


@frappe.whitelist()
def start_order(items, work_order, supplier="Home Ideas", gst_rate=5):
    if isinstance(items, str):
        items = json.loads(items)

    if not items:
        return {"success": False, "message": "No items provided"}

    order_ref = work_order
    if work_order and "F-" in work_order:
        parts = work_order.replace("F-", "").split("/")
        if parts:
            order_ref = parts[0]

    apo = frappe.get_doc({
        "doctype": "Automated Procurement Order",
        "supplier": supplier,
        "order_channel": "Supplier Portal",
        "supplier_portal": "DDecor",
        "work_order": work_order,
        "status": "Draft",
        "order_date": frappe.utils.now(),
        "company": "Fabrics And More",
        "triggered_by": "Manual",
        "triggered_by_user": frappe.session.user,
        "order_reference": order_ref,
        "custom_gst_rate": int(gst_rate),
        "total_items": len(items),
        "total_quantity": round(sum(float(i.get("qty", 0)) for i in items), 2),
        "total_amount": round(sum(float(i.get("qty", 0)) * float(i.get("rate", 0)) for i in items), 2),
        "custom_progress": json.dumps({
            "steps": [{"step": "Order queued", "status": "active", "time": str(frappe.utils.now())}],
            "completed_items": 0,
            "total_items": len(items),
            "current_item": "",
            "error": None
        })
    })

    for item in items:
        apo.append("items", {
            "item_code": item.get("item_code", ""),
            "item_name": item.get("item_name", ""),
            "quantity": float(item.get("qty", 0)),
            "rate": float(item.get("rate", 0)),
            "amount": round(float(item.get("qty", 0)) * float(item.get("rate", 0)), 2),
            "collection": item.get("collection", ""),
            "serial_number": item.get("serial_number", ""),
            "work_order": item.get("work_order", work_order),
            "shortage_qty": float(item.get("qty", 0)),
            "item_status": "Pending",
            "source_table": item.get("source_table", "Main")
        })

    apo.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "ddecor_ordering.order_placer.place_order",
        queue="long",
        timeout=1800,
        apo_name=apo.name
    )

    return {
        "success": True,
        "message": "Order " + apo.name + " queued — " + str(len(items)) + " items. Track in In Progress tab.",
        "apo_name": apo.name
    }


@frappe.whitelist()
def get_order_progress(apo_name):
    try:
        apo = frappe.get_doc("Automated Procurement Order", apo_name)
        progress = {}
        if apo.custom_progress:
            progress = json.loads(apo.custom_progress)

        order_result = []
        if apo.custom_order_result:
            try:
                order_result = json.loads(apo.custom_order_result)
            except:
                pass

        return {
            "success": True,
            "apo_name": apo_name,
            "status": apo.status,
            "progress": progress,
            "order_result": order_result,
            "portal_po_numbers": apo.custom_portal_order_number or "",
            "purchase_order": apo.purchase_order or "",
            "error": apo.last_error or ""
        }
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


@frappe.whitelist()
def get_active_orders():
    orders = frappe.get_all(
        "Automated Procurement Order",
        filters={
            "order_channel": "Supplier Portal",
            "status": ["in", ["Draft", "Ordered"]]
        },
        fields=["name", "supplier", "work_order", "status", "order_date",
                "custom_progress", "last_error", "order_channel",
                "custom_order_result", "purchase_order", "custom_portal_order_number",
                "triggered_by_user"],
        order_by="creation desc",
        limit=20
    )

    result = []
    for o in orders:
        progress = {}
        if o.custom_progress:
            try:
                progress = json.loads(o.custom_progress)
            except:
                pass

        order_result = []
        if o.custom_order_result:
            try:
                order_result = json.loads(o.custom_order_result)
            except:
                pass

        items = frappe.get_all(
            "Automated Procurement Order Item",
            filters={"parent": o.name},
            fields=["item_code", "item_name", "quantity", "rate", "amount",
                    "collection", "serial_number", "portal_po_number",
                    "source_table", "item_status"]
        )

        result.append({
            "name": o.name,
            "supplier": o.supplier,
            "work_order": o.work_order,
            "status": o.status,
            "order_date": str(o.order_date or ""),
            "channel": "Supplier Portal",
            "progress": progress,
            "order_result": order_result,
            "items": items,
            "portal_po_numbers": o.custom_portal_order_number or "",
            "purchase_order": o.purchase_order or "",
            "triggered_by": (o.triggered_by_user or "").split("@")[0],
            "error": o.last_error or ""
        })

    return {
        "success": True,
        "count": len(result),
        "orders": result
    }


@frappe.whitelist()
def get_order_history(supplier="", status="", search=""):
    import frappe
    from frappe.utils import time_diff_in_seconds

    filters = {}
    if supplier:
        filters["supplier"] = supplier
    if status:
        filters["status"] = status
    else:
        filters["status"] = ["in", ["Ordered", "Failed", "Cancelled", "Received", "Confirmed", "Partially Received"]]
    if search:
        filters["name"] = ["like", f"%{search}%"]

    orders = frappe.get_all(
        "Automated Procurement Order",
        filters=filters,
        fields=["name", "supplier", "supplier_name", "status", "order_date",
                "total_items", "total_amount", "total_quantity",
                "triggered_by_user", "triggered_by",
                "execution_started", "execution_completed", "order_channel",
                "work_order", "work_order_name", "ccp_order_id",
                "purchase_order", "last_error", "creation"],
        order_by="creation desc",
        limit=50
    )

    result = []
    for o in orders:
        # ── Duration ──
        duration = ""
        if o.execution_started and o.execution_completed:
            try:
                secs = time_diff_in_seconds(o.execution_completed, o.execution_started)
                duration = f"{round(secs/60,1)}m" if secs <= 86400 else f"{round(secs/86400,1)}d"
            except Exception:
                pass

        # ── Channel label ──
        channel = o.order_channel or ""
        if channel in ("Supplier Portal", "DDecor", "DDécor"):
            channel_label = "DDécor Portal"
        elif channel == "WhatsApp":
            channel_label = "WhatsApp"
        else:
            channel_label = channel or "Manual"

        # ── Work Order aggregate status (live) ──
        wo_aggregate = ""
        wo_display = o.work_order_name or o.work_order or ""
        if o.work_order:
            try:
                wo_aggregate = frappe.db.get_value("Work Order", o.work_order, "custom_ingredients_status") or ""
            except Exception:
                wo_aggregate = ""

        # ── APO line items + their CURRENT ingredient status on the WO ──
        items_out = []
        try:
            apo_items = frappe.get_all(
                "Automated Procurement Order Item",
                filters={"parent": o.name},
                fields=["item_code", "item_name", "quantity", "rate", "amount",
                        "collection", "serial_number", "work_order"]
            )
            for it in apo_items:
                # Look up the live ingredient status for this item on its WO
                item_status = ""
                wo_for_item = it.get("work_order") or o.work_order
                if wo_for_item and it.get("item_code"):
                    # Check main table first, then rework, then additional
                    row = frappe.db.get_value(
                        "Work Order Item",
                        {"parent": wo_for_item, "item_code": it["item_code"]},
                        "custom_item_ingredient_status"
                    )
                    if not row:
                        # rework / additional don't carry ingredient status; mark ordered
                        row = ""
                    item_status = row or ""
                items_out.append({
                    "item_code": it.get("item_code", ""),
                    "item_name": it.get("item_name", ""),
                    "collection": it.get("collection", ""),
                    "serial_number": it.get("serial_number", ""),
                    "qty": it.get("quantity", 0),
                    "rate": it.get("rate", 0),
                    "amount": it.get("amount", 0),
                    "status": item_status,
                })
        except Exception:
            pass

        # ── Purchase Order details (live) ──
        po_info = None
        portal_po = ""
        if o.purchase_order:
            try:
                po_doc = frappe.db.get_value(
                    "Purchase Order", o.purchase_order,
                    ["status", "docstatus", "grand_total", "supplier"], as_dict=True
                )
                if po_doc:
                    ds_map = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
                    po_info = {
                        "name": o.purchase_order,
                        "docstatus_label": ds_map.get(po_doc.docstatus, "?"),
                        "status": po_doc.status or "",
                        "grand_total": po_doc.grand_total or 0,
                    }
                # portal PO number stored on the PO (DDécor)
                portal_po = frappe.db.get_value(
                    "Purchase Order", o.purchase_order, "custom_po_number__factory"
                ) or ""
            except Exception:
                pass

        # ── WhatsApp message reference (if any) ──
        wa_msg = ""
        if channel == "WhatsApp":
            try:
                wa = frappe.get_all(
                    "WhatsApp Message",
                    filters={"reference_doctype": "Automated Procurement Order",
                             "reference_document_name": o.name},
                    fields=["name", "status", "to"],
                    limit=1
                )
                if wa:
                    wa_msg = wa[0].get("status", "") or "Sent"
            except Exception:
                pass

        result.append({
            "name": o.name,
            "order_date": str(o.order_date or o.creation or ""),
            "supplier": o.supplier or "",
            "supplier_name": o.supplier_name or o.supplier or "",
            "channel": channel,
            "channel_label": channel_label,
            "items_count": o.total_items or len(items_out),
            "items": items_out,
            "amount": o.total_amount or 0,
            "total_qty": o.total_quantity or 0,
            "status": o.status or "",
            "duration": duration,
            "user": o.triggered_by_user or "",
            "triggered_by": o.triggered_by or "",
            "work_order": o.work_order or "",
            "work_order_name": wo_display,
            "wo_aggregate_status": wo_aggregate,
            "ccp_order_id": o.ccp_order_id or "",
            "purchase_order": o.purchase_order or "",
            "po_info": po_info,
            "portal_po_numbers": portal_po,
            "whatsapp_status": wa_msg,
            "error": o.last_error or "",
        })

    return {"success": True, "orders": result}


# ============================================================
# Fabric Orders Excel export — added for hub "Download Excel" button
# method: ddecor_ordering.api.export_fabric_orders_sheet
# ============================================================
import datetime as _dt
import io as _io
from openpyxl import Workbook as _Workbook
from openpyxl.styles import Font as _Font, PatternFill as _PatternFill, Alignment as _Alignment, Border as _Border, Side as _Side
from openpyxl.utils import get_column_letter as _gcl


@frappe.whitelist()
def export_fabric_orders_sheet():
    apos = frappe.db.sql("""
        SELECT name, ccp_order_id, supplier, order_channel, order_date, status,
               work_order, work_order_name, purchase_order, triggered_by_user, owner,
               custom_portal_order_number, custom_portal_invoice_number
        FROM `tabAutomated Procurement Order`
        WHERE status = 'Ordered'
        ORDER BY order_date DESC
    """, as_dict=1)

    closed_pos = {}
    for r in frappe.db.sql("""
        SELECT DISTINCT pri.purchase_order po
        FROM `tabPurchase Receipt Item` pri
        JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE pr.docstatus = 1 AND pri.purchase_order IS NOT NULL AND pri.purchase_order != ''
    """, as_dict=1):
        closed_pos[r["po"]] = 1

    data = []
    for a in apos:
        po = a.get("purchase_order") or ""
        is_closed = bool(po and po in closed_pos)
        wo = a.get("work_order") or ""
        wo_agg = frappe.db.get_value("Work Order", wo, "custom_ingredients_status") if wo else ""
        portal_no = a.get("custom_portal_order_number") or a.get("custom_portal_invoice_number") or ""
        if a.get("order_channel") != "Supplier Portal":
            portal_no = ""
        ccp = a.get("ccp_order_id") or ""
        if not ccp and wo:
            ccp = frappe.db.get_value("Work Order", wo, "custom_ccp_id") or ""
        items = frappe.db.sql("""
            SELECT item_code, item_name, quantity
            FROM `tabAutomated Procurement Order Item` WHERE parent = %s
        """, (a["name"],), as_dict=1)
        for it in items:
            data.append({
                "ccp_id": ccp,
                "fabric": it.get("item_name") or it.get("item_code") or "",
                "qty": it.get("quantity") or 0,
                "supplier": a.get("supplier") or "Unassigned",
                "portal_no": portal_no,
                "po": po,
                "order_date": str(a.get("order_date") or "")[:19],
                "ordered_by": a.get("triggered_by_user") or a.get("owner") or "",
                "open_closed": "Closed" if is_closed else "Open",
                "wo_total_status": wo_agg or "",
                "wo": a.get("work_order_name") or wo,
            })

    FONT = "Arial"
    HDR_FILL = _PatternFill("solid", fgColor="1F3864")
    HDR_FONT = _Font(name=FONT, bold=True, color="FFFFFF", size=10)
    TITLE_FONT = _Font(name=FONT, bold=True, color="1F3864", size=14)
    SUB_FONT = _Font(name=FONT, italic=True, color="595959", size=9)
    CELL_FONT = _Font(name=FONT, size=10)
    thin = _Side(style="thin", color="D9D9D9")
    BORDER = _Border(left=thin, right=thin, top=thin, bottom=thin)
    OPEN_FILL = _PatternFill("solid", fgColor="FFF2CC")
    CLOSED_FILL = _PatternFill("solid", fgColor="C6EFCE")
    SFILL = {"IN_STOCK": "C6EFCE", "IN_STOCK_TENTATIVE": "D9EAD3", "EXPECTED": "BDD7EE",
             "COM_EXPECTED": "D9D2E9", "TEMPLATE_EXPECTED": "D0E0E3", "NOT_AVAILABLE": "FFC7CE",
             "REVIEW_REQUIRED": "FFE699", "NO_RECIPE": "D9D9D9"}
    SFONT = {"IN_STOCK": "006100", "IN_STOCK_TENTATIVE": "375623", "EXPECTED": "1F4E78",
             "COM_EXPECTED": "5B3A8A", "TEMPLATE_EXPECTED": "0B5394", "NOT_AVAILABLE": "9C0006",
             "REVIEW_REQUIRED": "7F6000", "NO_RECIPE": "595959"}

    wb = _Workbook()
    ws = wb.active
    ws.title = "All Orders"
    H1 = ["CCP ID", "Fabric", "Qty", "Supplier", "Portal No.", "PO Number", "Order Date", "Ordered By", "Status"]
    n1 = len(H1)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n1)
    ws.cell(row=1, column=1, value="All Fabrics Ordered").font = TITLE_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n1)
    ws.cell(row=2, column=1, value="Confirmed orders (status = Ordered)  -  " + _dt.date.today().isoformat() + "  -  " + str(len(data)) + " line items").font = SUB_FONT
    for j in range(n1):
        c = ws.cell(row=3, column=j + 1, value=H1[j])
        c.fill = HDR_FILL
        c.font = HDR_FONT
        c.alignment = _Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[3].height = 26
    r = 4
    for d in data:
        ws.cell(row=r, column=1, value=d["ccp_id"]).font = CELL_FONT
        ws.cell(row=r, column=2, value=d["fabric"]).font = CELL_FONT
        qc = ws.cell(row=r, column=3, value=d["qty"])
        qc.font = CELL_FONT
        qc.number_format = "0.00"
        qc.alignment = _Alignment(horizontal="right")
        ws.cell(row=r, column=4, value=d["supplier"]).font = CELL_FONT
        pc = ws.cell(row=r, column=5, value=d["portal_no"])
        pc.font = CELL_FONT
        pc.alignment = _Alignment(horizontal="center")
        ws.cell(row=r, column=6, value=d["po"]).font = CELL_FONT
        ws.cell(row=r, column=7, value=d["order_date"]).font = CELL_FONT
        ws.cell(row=r, column=8, value=d["ordered_by"]).font = CELL_FONT
        oc = ws.cell(row=r, column=9, value=d["open_closed"])
        oc.font = _Font(name=FONT, size=9, bold=True, color=("7F6000" if d["open_closed"] == "Open" else "006100"))
        oc.fill = OPEN_FILL if d["open_closed"] == "Open" else CLOSED_FILL
        oc.alignment = _Alignment(horizontal="center")
        for col in range(1, 10):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    w1 = [16, 48, 8, 24, 18, 20, 19, 28, 11]
    for j in range(n1):
        ws.column_dimensions[_gcl(j + 1)].width = w1[j]
    ws.freeze_panes = ws.cell(row=4, column=1)
    ws.auto_filter.ref = "A3:" + _gcl(n1) + str(r - 1)

    H2 = ["CCP ID", "Fabric", "Qty", "Portal No.", "PO Number", "Open/Closed", "Order Date", "WO Total Status", "Work Order"]
    n2 = len(H2)
    w2 = [16, 48, 8, 18, 20, 12, 19, 18, 20]
    sup = {}
    for d in data:
        sup.setdefault(d["supplier"], []).append(d)

    for s in sorted(sup.keys()):
        rows = sup[s]
        nm = s
        for ch in "[]:*?/\\":
            nm = nm.replace(ch, " ")
        nm = nm[:31]
        wsx = wb.create_sheet(nm)
        tot = round(sum((x["qty"] or 0) for x in rows), 2)
        wsx.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n2)
        wsx.cell(row=1, column=1, value=s).font = TITLE_FONT
        wsx.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n2)
        wsx.cell(row=2, column=1, value=str(len(rows)) + " line items  -  total qty " + str(tot) + " m").font = SUB_FONT
        for j in range(n2):
            c = wsx.cell(row=3, column=j + 1, value=H2[j])
            c.fill = HDR_FILL
            c.font = HDR_FONT
            c.alignment = _Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = BORDER
        wsx.row_dimensions[3].height = 26
        r = 4
        for d in rows:
            wsx.cell(row=r, column=1, value=d["ccp_id"]).font = CELL_FONT
            wsx.cell(row=r, column=2, value=d["fabric"]).font = CELL_FONT
            qc = wsx.cell(row=r, column=3, value=d["qty"])
            qc.font = CELL_FONT
            qc.number_format = "0.00"
            qc.alignment = _Alignment(horizontal="right")
            pc = wsx.cell(row=r, column=4, value=d["portal_no"])
            pc.font = CELL_FONT
            pc.alignment = _Alignment(horizontal="center")
            wsx.cell(row=r, column=5, value=d["po"]).font = CELL_FONT
            oc = wsx.cell(row=r, column=6, value=d["open_closed"])
            oc.font = _Font(name=FONT, size=9, bold=True, color=("7F6000" if d["open_closed"] == "Open" else "006100"))
            oc.fill = OPEN_FILL if d["open_closed"] == "Open" else CLOSED_FILL
            oc.alignment = _Alignment(horizontal="center")
            wsx.cell(row=r, column=7, value=d["order_date"]).font = CELL_FONT
            sv = (d["wo_total_status"] or "").upper().replace(" ", "_")
            if sv not in SFILL:
                sv = ""
            sc = wsx.cell(row=r, column=8, value=d["wo_total_status"])
            sc.fill = _PatternFill("solid", fgColor=SFILL.get(sv, "F2F2F2"))
            sc.font = _Font(name=FONT, size=9, bold=True, color=SFONT.get(sv, "808080"))
            sc.alignment = _Alignment(horizontal="center", vertical="center")
            sc.border = BORDER
            wsx.cell(row=r, column=9, value=d["wo"]).font = CELL_FONT
            for col in range(1, 10):
                wsx.cell(row=r, column=col).border = BORDER
            r += 1
        for j in range(n2):
            wsx.column_dimensions[_gcl(j + 1)].width = w2[j]
        wsx.freeze_panes = wsx.cell(row=4, column=1)
        wsx.auto_filter.ref = "A3:" + _gcl(n2) + str(r - 1)

    buf = _io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    fname = "Fabric_Orders_" + _dt.datetime.now().strftime("%Y-%m-%d_%H%M") + ".xlsx"
    filedoc = frappe.get_doc({
        "doctype": "File",
        "file_name": fname,
        "is_private": 0,
        "content": content,
    })
    filedoc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"file_url": filedoc.file_url, "file_name": fname, "rows": len(data)}
