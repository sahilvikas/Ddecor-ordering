"""
DDécor Portal Order Placer v8
Each item: Portal order → read PO number → create ERPNext PO → submit → verify EXPECTED
Runs as Frappe background job. All config from DDecor Settings doctype.

Changes from v7:
- source_table → sets custom_is_rework_po / custom_is_additional_po on ERPNext PO
- CCP order ID extraction for portal ref (F-43-38902-21/1-1 → 38902)
- Activity comments on Work Order and Purchase Order after creation
"""

import time
import json
import frappe
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DDECOR_URL = "https://ddecor.my.site.com/Merchant/s/"

GST_TEMPLATES = {
    5: "GST 5% - FAM - FAM",
    12: "GST 12% - FAM - FAM",
    18: "GST 18% - FAM - FAM"
}


def get_settings():
    """Load all config from DDecor Settings doctype."""
    s = frappe.get_single("DDecor Settings")
    return {
        "live_mode": s.live_mode == 1,
        "auto_submit_po": s.auto_submit_erp_po == 1 if hasattr(s, 'auto_submit_erp_po') else True,
        "headless": s.headless_browser == 1 if hasattr(s, 'headless_browser') else True,
        "gst_rate": int(s.default_gst_rate or 5),
        "max_items": int(s.max_items_per_run or 20),
        "username": s.portal_username or "",
        "password": s.get_password("portal_password") if s.portal_password else "",
        "supplier": s.erp_supplier or "Home Ideas",
        "warehouse": s.default_warehouse or "Main Warehouse - FAM",
        "company": s.company or "Fabrics And More",
        "login_timeout": int(s.login_timeout or 30) * 1000,
        "page_timeout": int(s.page_load_timeout or 20) * 1000,
        "dropdown_settle": float(s.dropdown_settle_time or 3),
        "between_delay": float(s.between_items_delay or 1),
        "notify_success": s.notify_on_success == 1 if hasattr(s, 'notify_on_success') else True,
        "notify_failure": s.notify_on_failure == 1 if hasattr(s, 'notify_on_failure') else True,
        "notify_emails": s.notification_emails or "",
    }


# ══════════════════════════════════════════════════════════════
# SALESFORCE UI HELPERS
# ══════════════════════════════════════════════════════════════

def kill_spinner(page):
    page.evaluate("""
        document.querySelectorAll('lightning-spinner, .slds-spinner_container').forEach(el => {
            el.style.display = 'none'; el.remove();
        });
    """)
    time.sleep(0.3)


def wait_for_spinner_cycle(page):
    try:
        page.locator("lightning-spinner").first.wait_for(state="visible", timeout=1500)
    except:
        time.sleep(0.5)
        return
    try:
        page.locator("lightning-spinner").first.wait_for(state="hidden", timeout=10000)
    except:
        kill_spinner(page)
    time.sleep(0.3)


def clear_field(page, field_name):
    try:
        clear_btns = page.locator("button.slds-combobox__input-entity-icon")
        for i in range(clear_btns.count()):
            if clear_btns.nth(i).is_visible():
                clear_btns.nth(i).click()
                time.sleep(0.3)
                wait_for_spinner_cycle(page)
    except:
        pass
    try:
        field = page.get_by_role("searchbox", name=field_name)
        field.click()
        time.sleep(0.2)
        field.press("Control+a")
        time.sleep(0.1)
        field.press("Backspace")
        time.sleep(0.3)
        wait_for_spinner_cycle(page)
    except:
        pass


def find_in_dropdown(page, target_text):
    target_upper = target_text.upper().strip()
    try:
        options = page.locator("[role='listbox'] [role='option'], .slds-listbox__item")
        count = options.count()
        if count == 0:
            options = page.locator("span").filter(has_text=target_upper.split()[0] if target_upper else "")
            count = options.count()

        option_texts = []
        for i in range(min(count, 20)):
            try:
                el = options.nth(i)
                if el.is_visible():
                    txt = el.inner_text().strip().upper()
                    if txt and txt != "NO RESULTS FOUND" and len(txt) > 1:
                        option_texts.append((txt, i))
            except:
                continue

        if not option_texts:
            return None

        for txt, idx in option_texts:
            if txt == target_upper:
                options.nth(idx).click(timeout=2000)
                time.sleep(0.5)
                wait_for_spinner_cycle(page)
                return txt

        matches = []
        for txt, idx in option_texts:
            if txt in target_upper or target_upper.startswith(txt):
                matches.append((len(txt), txt, idx))
        if matches:
            matches.sort(reverse=True)
            _, best_txt, best_idx = matches[0]
            options.nth(best_idx).click(timeout=2000)
            time.sleep(0.5)
            wait_for_spinner_cycle(page)
            return best_txt
    except:
        pass

    try:
        match = page.locator("span").filter(has_text=target_upper)
        for nth in [1, 0, 2]:
            if nth < match.count():
                try:
                    el = match.nth(nth)
                    if el.is_visible():
                        el.click(timeout=2000)
                        time.sleep(0.5)
                        wait_for_spinner_cycle(page)
                        return target_upper
                except:
                    continue
    except:
        pass
    return None


def type_and_select(page, field_name, target_text):
    clear_field(page, field_name)
    time.sleep(0.3)
    field = page.get_by_role("searchbox", name=field_name)
    field.click()
    time.sleep(0.5)

    target_upper = target_text.upper().strip()

    for i, char in enumerate(target_text.lower()):
        page.keyboard.type(char)
        page.keyboard.press("Enter")
        wait_for_spinner_cycle(page)

        try:
            options = page.locator("[role='listbox'] [role='option'], .slds-listbox__item")
            count = options.count()
            for j in range(min(count, 20)):
                el = options.nth(j)
                if el.is_visible():
                    txt = el.inner_text().strip().upper()
                    if txt == target_upper:
                        el.click(timeout=2000)
                        time.sleep(0.5)
                        wait_for_spinner_cycle(page)
                        return txt
        except:
            pass

        if i >= 10:
            break

    time.sleep(1)
    return find_in_dropdown(page, target_text)


def select_serial_dropdown(page, serial):
    clear_field(page, "Serial Number")
    time.sleep(0.3)
    field = page.get_by_role("searchbox", name="Serial Number")
    field.click()
    time.sleep(0.3)

    for char in serial:
        page.keyboard.type(char)
        page.keyboard.press("Enter")
        wait_for_spinner_cycle(page)

    time.sleep(1)

    try:
        page.get_by_text(serial, exact=True).click(timeout=3000)
        time.sleep(0.5)
        wait_for_spinner_cycle(page)
        return True
    except:
        pass

    for nth in [1, 2, 0, 3]:
        try:
            page.locator("span").filter(has_text=serial).nth(nth).click(timeout=2000)
            time.sleep(0.5)
            wait_for_spinner_cycle(page)
            return True
        except:
            continue
    return False


def enter_qty_js(page, quantity):
    """Fill the Quantity cell, located by its column header.

    DDécor added an 'Expected Stock Date' column at the front of the item
    table in Aug 2026. The old 'first empty input in the row' approach then
    filled that (disabled) date field instead, leaving Quantity blank — the
    portal rejected the order with 'Required field is missing: [Quantity]'.
    """
    QTY_COLUMN_HEADER = "Quantity"
    return page.evaluate(f"""
        (function() {{
            function norm(s) {{ return (s || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase(); }}
            var want = norm('{QTY_COLUMN_HEADER}');

            var tables = document.querySelectorAll('table');
            for (var t = 0; t < tables.length; t++) {{
                var thead = tables[t].querySelector('thead');
                if (!thead) continue;
                if (thead.innerText.indexOf('Quantity') === -1) continue;

                var ths = tables[t].querySelectorAll('thead th');
                var qtyCol = -1;
                for (var h = 0; h < ths.length; h++) {{
                    if (norm(ths[h].innerText) === want) {{ qtyCol = h; break; }}
                }}
                if (qtyCol === -1) return 'no_quantity_column';

                var rows = tables[t].querySelectorAll('tbody tr');
                if (rows.length === 0) return 'no_rows';
                var lastRow = rows[rows.length - 1];
                var cells = lastRow.querySelectorAll('td');
                if (qtyCol >= cells.length) return 'qty_col_out_of_range';

                var inputs = cells[qtyCol].querySelectorAll('input');
                for (var i = 0; i < inputs.length; i++) {{
                    var inp = inputs[i];
                    if (inp.disabled || inp.readOnly) continue;
                    inp.focus();
                    inp.value = '{quantity}';
                    inp.dispatchEvent(new Event('input',  {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    inp.dispatchEvent(new Event('blur',   {{bubbles: true}}));
                    // read back — the field must actually hold the value.
                    // numeric compare — the portal normalises '2.0' to '2.00',
                    // so a string compare rejects a perfectly good fill.
                    var got  = parseFloat(inp.value);
                    var want = parseFloat('{quantity}');
                    if (!isNaN(got) && !isNaN(want) && Math.abs(got - want) < 0.001)
                        return 'filled';
                    if (String(inp.value).trim() === String('{quantity}').trim())
                        return 'filled';
                    return 'value_did_not_stick:' + inp.value;
                }}
                return 'no_editable_input_in_quantity_cell';
            }}
            return 'no_table';
        }})()
    """)


def verify_qty_entered(page, quantity):
    """Read the Quantity cell back. Returns (ok, actual_value)."""
    try:
        val = page.evaluate("""
            (function() {
                function norm(s) { return (s || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase(); }
                var tables = document.querySelectorAll('table');
                for (var t = 0; t < tables.length; t++) {
                    var thead = tables[t].querySelector('thead');
                    if (!thead || thead.innerText.indexOf('Quantity') === -1) continue;
                    var ths = tables[t].querySelectorAll('thead th');
                    var qtyCol = -1;
                    for (var h = 0; h < ths.length; h++)
                        if (norm(ths[h].innerText) === 'QUANTITY') { qtyCol = h; break; }
                    if (qtyCol === -1) return null;
                    var rows = tables[t].querySelectorAll('tbody tr');
                    if (!rows.length) return null;
                    var cells = rows[rows.length - 1].querySelectorAll('td');
                    if (qtyCol >= cells.length) return null;
                    var inputs = cells[qtyCol].querySelectorAll('input');
                    for (var i = 0; i < inputs.length; i++)
                        if (!inputs[i].disabled && !inputs[i].readOnly)
                            return inputs[i].value;
                    return null;
                }
                return null;
            })()
        """)
    except Exception:
        return False, None
    if val is None:
        return False, None
    try:
        return abs(float(val) - float(quantity)) < 0.001, val
    except (TypeError, ValueError):
        return str(val).strip() == str(quantity).strip(), val


def read_table_data(page):
    """Read the item row by COLUMN HEADER MAP rather than input position.

    Hardcoded positions broke when DDécor inserted 'Expected Stock Date' at
    the front of the table — unit_price started returning 'CL' (the Type
    column) and gst_pct returned the unit price.
    """
    try:
        return page.evaluate("""
            (function() {
                function norm(s) { return (s || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase(); }
                // header (normalised) → key we return
                var MAP = {
                    'COLLECTION':        'collection',
                    'SERIALNO':          'serial_no',
                    'QDS':               'qds',
                    'WHSTOCK':           'wh_stock',
                    'QUANTITY':          'quantity',
                    'UNIT':              'unit',
                    'TYPE':              'type',
                    'UNITPRICE':         'unit_price',
                    'GST':               'gst_pct',
                    'GSTPERCENT':        'gst_pct',
                    'EXPECTEDSTOCKDATE': 'expected_stock_date'
                };

                var tables = document.querySelectorAll('table');
                for (var t = 0; t < tables.length; t++) {
                    var thead = tables[t].querySelector('thead');
                    if (!thead || thead.innerText.indexOf('Quantity') === -1) continue;

                    var ths = tables[t].querySelectorAll('thead th');
                    var headers = [];
                    for (var h = 0; h < ths.length; h++) headers.push(norm(ths[h].innerText));

                    var rows = tables[t].querySelectorAll('tbody tr');
                    if (rows.length === 0) continue;
                    var lastRow = rows[rows.length - 1];
                    var cells = lastRow.querySelectorAll('td');

                    var result = {};
                    for (var c = 0; c < cells.length; c++) {
                        var key = MAP[headers[c]];
                        if (!key) continue;
                        var inputs = cells[c].querySelectorAll('input');
                        if (inputs.length > 0) {
                            result[key] = inputs[0].value;
                        } else {
                            result[key] = cells[c].innerText.trim();
                        }
                    }
                    // keep the raw header list — makes the next portal change obvious
                    result._headers = headers.join('|');
                    return result;
                }
                return null;
            })()
        """)
    except:
        return None


# ══════════════════════════════════════════════════════════════
# PROGRESS TRACKER
# ══════════════════════════════════════════════════════════════

class ProgressTracker:
    def __init__(self, apo_name):
        self.apo_name = apo_name
        self.steps = []
        self.completed_items = 0
        self.total_items = 0
        self.current_item = ""
        self.error = None

    def step(self, description):
        for s in self.steps:
            if s["status"] == "active":
                s["status"] = "done"
        self.steps.append({"step": description, "status": "active", "time": datetime.now().isoformat()})
        self._push()

    def done(self):
        for s in reversed(self.steps):
            if s["status"] == "active":
                s["status"] = "done"
                break
        self._push()

    def fail(self, description, error_msg):
        self.steps.append({"step": description, "status": "error", "time": datetime.now().isoformat(), "error": error_msg})
        self.error = error_msg
        self._push()

    def item_done(self, item_desc):
        self.completed_items += 1
        self.current_item = ""
        self.done()
        self._push()

    def _push(self):
        progress = {
            "steps": self.steps, "completed_items": self.completed_items,
            "total_items": self.total_items, "current_item": self.current_item,
            "error": self.error
        }
        try:
            frappe.db.set_value("Automated Procurement Order", self.apo_name,
                "custom_progress", json.dumps(progress), update_modified=False)
            frappe.db.commit()
        except:
            pass
        try:
            frappe.publish_realtime(event="ddecor_order_progress",
                message={"apo_name": self.apo_name, "progress": progress}, after_commit=False)
        except:
            pass


# ══════════════════════════════════════════════════════════════
# CREDENTIALS
# ══════════════════════════════════════════════════════════════

def get_credentials(cfg):
    """Get credentials from DDecor Settings."""
    username = cfg.get("username", "")
    password = cfg.get("password", "")
    if not username:
        frappe.throw("DDécor credentials not configured. Go to DDecor Settings.")
    return username, password


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def extract_ccp_order_id(wo_display_name):
    """
    Extract the CCP order ID from a WO display name for the DDécor portal ref field.
    F-43-38902-21/1-1 → 38902
    F-43-38902 → 38902
    MFG-WO-2026-12746 → MFG-WO-2026-12746 (no extraction possible)
    """
    if not wo_display_name:
        return wo_display_name or ""

    name = wo_display_name.strip()

    # Pattern: F-{company_id}-{ccp_order_id}-{suffix}
    if name.startswith("F-"):
        parts = name[2:].split("-")
        # parts[0] = company_id (43), parts[1] = ccp_order_id (38902), rest = suffix
        if len(parts) >= 2:
            return parts[1]

    return name


# ══════════════════════════════════════════════════════════════
# PORTAL NAVIGATION
# ══════════════════════════════════════════════════════════════

def do_login(page, tracker, username, password):
    tracker.step("Opening DDécor portal")
    page.goto(DDECOR_URL)
    try:
        page.get_by_role("textbox", name="Username").wait_for(timeout=15000)
    except PWTimeout:
        tracker.fail("Open portal", "Login page did not load")
        return False
    tracker.done()

    tracker.step("Entering credentials")
    page.get_by_role("textbox", name="Username").fill(username)
    page.get_by_role("textbox", name="Password").fill(password)
    tracker.done()

    tracker.step("Logging in")
    page.get_by_role("button", name="Log in").click()
    try:
        page.wait_for_selector("text=New Order", timeout=30000)
    except PWTimeout:
        tracker.fail("Login", "Dashboard did not load after login")
        return False
    time.sleep(2)
    kill_spinner(page)
    tracker.done()
    return True


def dismiss_blockers(page):
    """Close anything sitting on top of the page and blocking clicks.

    The portal intermittently raises a Salesforce "Sorry to interrupt /
    CSS Error" modal. While it is up, every click on the page is refused —
    which is how a resolved, visible link can still time out on click.
    """
    try:
        page.evaluate("""
            (function() {
                // close buttons on any visible dialog
                var dialogs = document.querySelectorAll(
                    '[role=dialog], .slds-modal, .modal-container');
                for (var i = 0; i < dialogs.length; i++) {
                    var r = dialogs[i].getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    var btns = dialogs[i].querySelectorAll(
                        'button, .slds-modal__close, [title=Close], [title=close]');
                    var clicked = false;
                    for (var b = 0; b < btns.length; b++) {
                        var t = (btns[b].innerText || btns[b].title || '').trim();
                        if (/close|×|refresh|ok|dismiss/i.test(t)) {
                            try { btns[b].click(); clicked = true; break; } catch (e) {}
                        }
                    }
                    if (!clicked) dialogs[i].remove();
                }
                // backdrops and spinners that swallow pointer events
                var junk = document.querySelectorAll(
                    '.slds-backdrop, .slds-backdrop_open, .modal-backdrop, '
                    + '.slds-spinner_container, lightning-spinner, .forceToastManager');
                for (var j = 0; j < junk.length; j++) junk[j].remove();
            })()
        """)
    except Exception:
        pass


def open_new_order_form(page, tracker):
    tracker.step("Opening new order form")

    dismiss_blockers(page)
    kill_spinner(page)

    # Navigate by URL rather than clicking the nav link. A click can be
    # refused by any overlay; a goto cannot. The href is read off the link
    # so nothing is hardcoded.
    navigated = False
    try:
        href = page.get_by_role("link", name="New Order").first.get_attribute("href")
        if href:
            if href.startswith("/"):
                origin = page.evaluate("window.location.origin")
                href = origin + href
            if href.startswith("http"):
                page.goto(href, wait_until="domcontentloaded", timeout=45000)
                navigated = True
    except Exception:
        navigated = False

    if not navigated:
        # fall back to the original click, forced past any overlay
        try:
            page.get_by_role("link", name="New Order").click(timeout=20000)
        except Exception:
            dismiss_blockers(page)
            page.get_by_role("link", name="New Order").click(force=True, timeout=20000)

    try:
        page.get_by_role("button", name="New").wait_for(timeout=20000)
    except PWTimeout:
        tracker.fail("New Order page", "New button did not appear")
        return False
    time.sleep(1)
    kill_spinner(page)
    dismiss_blockers(page)

    try:
        page.get_by_role("button", name="New").click(timeout=20000)
    except Exception:
        dismiss_blockers(page)
        kill_spinner(page)
        page.get_by_role("button", name="New").click(force=True, timeout=20000)
    try:
        page.get_by_role("textbox", name="Order ref no").wait_for(timeout=20000)
    except PWTimeout:
        tracker.fail("New order form", "Form did not load")
        return False
    time.sleep(4)
    kill_spinner(page)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)
    kill_spinner(page)
    tracker.done()
    return True


def fill_ref(page, tracker, ref_text):
    tracker.step(f"Setting order ref: {ref_text}")
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)
    ref_field = page.get_by_role("textbox", name="Order ref no")
    ref_field.click()
    ref_field.fill(str(ref_text))
    time.sleep(1)
    page.locator("body").click(position={"x": 10, "y": 10})
    time.sleep(2)
    kill_spinner(page)
    tracker.done()


def order_single_item(page, tracker, collection, serial, qty):
    """Select collection, serial, enter qty. Returns item data dict or None."""

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)
    kill_spinner(page)

    tracker.step("Waiting for Collection field")
    try:
        page.get_by_role("searchbox", name="Collection").wait_for(timeout=15000)
        time.sleep(0.5)
        kill_spinner(page)
    except PWTimeout:
        tracker.fail("Collection field", "Collection searchbox not visible")
        return None
    tracker.done()

    tracker.step(f"Selecting collection: {collection}")
    matched = type_and_select(page, "Collection", collection)
    if not matched:
        tracker.fail(f"Collection: {collection}", f"'{collection}' not found in dropdown")
        return None
    tracker.done()

    tracker.step(f"Selecting serial: {serial}")
    if not select_serial_dropdown(page, serial):
        tracker.fail(f"Serial: {serial}", f"'{serial}' not found in dropdown")
        return None
    tracker.done()

    tracker.step("Waiting for item table to render")
    time.sleep(3)
    wait_for_spinner_cycle(page)
    kill_spinner(page)
    time.sleep(2)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    table_check = page.evaluate("""
        (function() {
            var tables = document.querySelectorAll('table');
            for (var t = 0; t < tables.length; t++) {
                var h = tables[t].querySelector('thead') ? tables[t].querySelector('thead').innerText : '';
                if (h.indexOf('Quantity') !== -1) return 'found';
            }
            return 'not_found';
        })()
    """)
    if table_check != "found":
        tracker.fail("Item table", "Table did not render")
        return None
    tracker.done()

    tracker.step(f"Entering quantity: {qty}m")
    qty_result = enter_qty_js(page, qty)
    if qty_result != "filled":
        tracker.fail("Enter quantity", f"JS fill returned: {qty_result}")
        return None
    time.sleep(1)
    page.locator("body").click(position={"x": 10, "y": 10})
    time.sleep(2)
    wait_for_spinner_cycle(page)
    kill_spinner(page)

    # Read it back. The portal silently drops the value in some states, and a
    # blank Quantity is rejected only at submit time — by which point the old
    # code had already recorded the submit as successful.
    ok, actual = verify_qty_entered(page, qty)
    if not ok:
        tracker.fail("Verify quantity",
                     f"Quantity cell holds {actual!r}, expected {qty}")
        return None
    tracker.done()

    table_data = read_table_data(page) or {}
    return {
        "collection": collection,
        "serial_number": serial,
        "qty": qty,
        "wh_stock": table_data.get("wh_stock", ""),
        "unit_price": table_data.get("unit_price", ""),
        "gst_pct": table_data.get("gst_pct", "")
    }


# ══════════════════════════════════════════════════════════════
# SUBMIT / CANCEL ON PORTAL
# ══════════════════════════════════════════════════════════════

def cancel_current_order(page, tracker):
    """Click Cancel — dry run, no order placed."""
    tracker.step("Cancelling order (dry run)")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)
    try:
        cancel_buttons = page.get_by_role("button", name="Cancel")
        cancel_buttons.last.click()
        time.sleep(2)
        kill_spinner(page)
    except:
        pass
    tracker.done()
    return {"submitted": False, "po_number": None}


def submit_and_read_po(page, tracker):
    """Submit on portal, wait for redirect to PO page, read PO number + amounts."""
    tracker.step("Submitting order on DDécor portal")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    try:
        submit_buttons = page.get_by_role("button", name="Submit")
        submit_buttons.nth(1).click()
    except:
        try:
            page.get_by_role("button", name="Submit").last.click()
        except Exception as e:
            tracker.fail("Submit click", str(e)[:100])
            return {"submitted": False, "po_number": None, "error": str(e)[:100]}
    tracker.done()

    tracker.step("Waiting for order confirmation")
    po_data = {"submitted": True, "po_number": None, "subtotal": None, "tax": None, "total_cost": None}

    # The old wait was `text=Purchase Order`, which also matches the Purchase
    # Order LIST page and the nav — so it succeeded instantly, before the SPA
    # had routed to the new PO, and every PO-read method then ran against a
    # page with no PO number on it.
    #
    # First: did the portal reject the submission outright?
    try:
        err = page.evaluate("""
            (function() {
                var sels = ['.slds-notify', '.toastContainer', '[role=alert]'];
                for (var s = 0; s < sels.length; s++) {
                    var els = document.querySelectorAll(sels[s]);
                    for (var i = 0; i < els.length; i++) {
                        var r = els[i].getBoundingClientRect();
                        if (r.width === 0) continue;
                        var t = (els[i].innerText || '').trim();
                        if (!t) continue;
                        if (/Required field is missing|Error!|is missing|not valid|Please enter/i.test(t))
                            return t.substring(0, 200);
                    }
                }
                return null;
            })()
        """)
    except:
        err = None
    if err:
        tracker.fail("Order rejected by portal", err.replace("\n", " ")[:150])
        po_data["submitted"] = False
        po_data["error"] = "Portal rejected the order: " + err.replace("\n", " ")[:150]
        return po_data

    # Then: poll until the PO detail page has actually rendered.
    confirmed = False
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            kill_spinner(page)
            hit = page.evaluate("""
                (function() {
                    if (/\\/po\\d{4,9}(\\/|$|\\?)/i.test(window.location.href)) return 'url';
                    var m = (document.title || '').match(/PO-\\d{5,7}/);
                    if (m) return 'title';
                    var all = document.querySelectorAll('lightning-formatted-text, slot, span, h1, h2');
                    for (var i = 0; i < all.length; i++) {
                        var t = (all[i].innerText || '').trim();
                        if (/^PO-\\d{5,7}$/.test(t)) return 'element';
                    }
                    return null;
                })()
            """)
            if hit:
                confirmed = True
                break
        except:
            pass
        time.sleep(1)

    if not confirmed:
        tracker.fail("Order confirmation",
                     "PO page did not render within 60s after submit")
        po_data["error"] = "Confirmation page timeout"
        po_data["needs_manual_check"] = True
        return po_data

    time.sleep(2)
    kill_spinner(page)
    time.sleep(1)
    tracker.done()

    # Read PO number
    tracker.step("Reading PO number from confirmation")

    # Method 1: URL — .../po503190
    try:
        url = page.url
        po_data["po_url"] = url
        if "/po" in url.lower():
            last_part = url.rstrip("/").split("/")[-1]
            if last_part.lower().startswith("po"):
                po_data["po_number"] = "PO-" + last_part[2:]
    except:
        pass

    # Method 2: Page header slot
    if not po_data["po_number"]:
        try:
            po_text = page.locator("slot").nth(2).inner_text().strip()
            if po_text.startswith("PO-"):
                po_data["po_number"] = po_text
        except:
            pass

    # Method 3: Purchase Order Name field
    if not po_data["po_number"]:
        try:
            po_field = page.locator("lightning-formatted-text").filter(has_text="PO-")
            if po_field.count() > 0:
                po_data["po_number"] = po_field.first.inner_text().strip()
        except:
            pass

    # Method 4: JS regex
    if not po_data["po_number"]:
        try:
            po_data["po_number"] = page.evaluate("""
                (function() {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var t = (all[i].innerText || '').trim();
                        if (/^PO-\\d{5,7}$/.test(t)) return t;
                    }
                    var title = document.title || '';
                    var match = title.match(/PO-\\d{5,7}/);
                    if (match) return match[0];
                    return null;
                })()
            """)
        except:
            pass

    # Read amounts
    try:
        amounts = page.evaluate("""
            (function() {
                var result = {};
                var texts = document.body.innerText;
                var sub = texts.match(/Subtotal[\\s\\n]*INR ([\\d,\\.]+)/);
                if (sub) result.subtotal = sub[1];
                var tax = texts.match(/Tax[\\s\\n]*INR ([\\d,\\.]+)/);
                if (tax) result.tax = tax[1];
                var total = texts.match(/Total Cost[\\s\\n]*INR ([\\d,\\.]+)/);
                if (total) result.total_cost = total[1];
                return result;
            })()
        """)
        if amounts:
            po_data["subtotal"] = amounts.get("subtotal")
            po_data["tax"] = amounts.get("tax")
            po_data["total_cost"] = amounts.get("total_cost")
    except:
        pass

    if po_data["po_number"]:
        tracker.done()
        tracker.step(f"Portal order confirmed: {po_data['po_number']}")
        tracker.done()
    else:
        # We got to a confirmation page but could not read the number. The
        # order most likely EXISTS at DDécor — flag it so nobody requeues it
        # into a duplicate.
        tracker.fail("Read PO number",
                     "Order placed but PO number unreadable — check the portal "
                     "before requeuing")
        po_data["error"] = ("Order placed but PO number unreadable — verify on "
                            "the DDécor portal before requeuing")
        po_data["needs_manual_check"] = True

    return po_data


# ══════════════════════════════════════════════════════════════
# ERPNEXT PURCHASE ORDER CREATION
# ══════════════════════════════════════════════════════════════

def create_erp_purchase_order(tracker, item_row, work_order_name, portal_po_number,
                               gst_rate, wo_display_name, cfg, source_table="Main",
                               triggered_by_user=""):
    """
    Create and submit an ERPNext Purchase Order for one item.
    Sets rework/additional PO flags based on source_table.
    Logs activity on the WO and PO.
    """
    tracker.step(f"Creating ERPNext PO for {item_row.item_code}")

    item_code = item_row.item_code
    qty = item_row.quantity or 1
    supplier = cfg["supplier"]
    company = cfg["company"]
    warehouse = cfg["warehouse"]
    auto_submit = cfg["auto_submit_po"]

    # Get rate — Item Price first, then fallbacks
    rate = frappe.db.get_value("Item Price",
        {"item_code": item_code, "price_list": "Standard Buying"},
        "price_list_rate") or 0

    if not rate:
        rate = frappe.db.get_value("Item Price",
            {"item_code": item_code, "buying": 1},
            "price_list_rate") or 0

    if not rate:
        rate = frappe.db.get_value("Item", item_code, "last_purchase_rate") or 0

    if not rate:
        rate = frappe.db.get_value("Bin",
            {"item_code": item_code, "warehouse": warehouse},
            "valuation_rate") or 0

    if not rate:
        rate = item_row.rate or 0

    # Build PO custom name
    po_custom_name = ""
    if wo_display_name and wo_display_name.startswith("F-"):
        po_custom_name = "PO-" + wo_display_name[2:]

    uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Meter"

    gst_rate_int = int(gst_rate or 5)
    tax_template_name = GST_TEMPLATES.get(gst_rate_int, GST_TEMPLATES[5])

    # Determine source table flags
    src = (source_table or "Main").strip()
    is_rework = 1 if src == "Rework" else 0
    is_additional = 1 if src == "Additional" else 0

    try:
        po = frappe.new_doc("Purchase Order")
        po.supplier = supplier
        po.company = company
        po.currency = "INR"
        po.schedule_date = frappe.utils.add_days(frappe.utils.nowdate(), 7)
        po.custom_work_order_reference = work_order_name
        po.custom_po_number__factory = portal_po_number or ""
        po.custom_is_rework_po = is_rework
        po.custom_is_additional_po = is_additional

        if po_custom_name:
            po.custom_purchase_order_name = po_custom_name

        # Build remarks with who ordered
        user_label = triggered_by_user.split("@")[0] if triggered_by_user else "system"
        po.remarks = (
            f"Auto-created via DDécor portal by {user_label}\n"
            f"Portal PO: {portal_po_number or 'N/A'}\n"
            f"Work Order: {work_order_name}\n"
            f"Source: {src} materials"
        )

        po.append("items", {
            "item_code": item_code,
            "item_name": item_row.item_name or item_code,
            "qty": float(qty),
            "uom": uom,
            "rate": float(rate),
            "schedule_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
            "warehouse": warehouse
        })

        # Apply GST
        try:
            tax_doc = frappe.get_doc("Purchase Taxes and Charges Template", tax_template_name)
            po.taxes_and_charges = tax_template_name
            for tax_row in tax_doc.taxes:
                po.append("taxes", {
                    "charge_type": tax_row.charge_type,
                    "account_head": tax_row.account_head,
                    "description": tax_row.description,
                    "rate": tax_row.rate,
                    "included_in_print_rate": 0
                })
        except Exception as tax_err:
            frappe.log_error(title="DDécor PO - GST Template Error", message=str(tax_err))

        po.insert(ignore_permissions=True)
        tracker.done()

        # Submit if auto-submit enabled
        if auto_submit:
            tracker.step(f"Submitting ERPNext PO: {po.name}")
            po.submit()
            frappe.db.commit()
            tracker.done()
        else:
            tracker.step(f"ERPNext PO created as Draft: {po.name}")
            frappe.db.commit()
            tracker.done()

        # ── Log activity on the Purchase Order ──
        try:
            po_comment = (
                f"<b>Auto-created via DDécor Ordering Hub</b><br>"
                f"Placed by: <b>{triggered_by_user or 'system'}</b><br>"
                f"DDécor Portal PO: <b>{portal_po_number or 'N/A'}</b><br>"
                f"Item: {item_row.item_name or item_code} ({item_code})<br>"
                f"Qty: {qty} {uom} @ ₹{rate}<br>"
                f"Source: {src} materials<br>"
                f"Work Order: <a href='/app/work-order/{work_order_name}'>{wo_display_name or work_order_name}</a>"
            )
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "Purchase Order",
                "reference_name": po.name,
                "content": po_comment,
                "comment_email": triggered_by_user or frappe.session.user
            }).insert(ignore_permissions=True)
        except:
            pass

        # ── Log activity on the Work Order ──
        try:
            wo_comment = (
                f"<b>📦 DDécor Order Placed</b><br>"
                f"Placed by: <b>{triggered_by_user or 'system'}</b><br>"
                f"Item: {item_row.item_name or item_code}<br>"
                f"Qty: {qty} {uom}<br>"
                f"DDécor Portal PO: <b>{portal_po_number or 'N/A'}</b><br>"
                f"ERPNext PO: <a href='/app/purchase-order/{po.name}'>{po_custom_name or po.name}</a><br>"
                f"Source: {src} materials"
            )
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "Work Order",
                "reference_name": work_order_name,
                "content": wo_comment,
                "comment_email": triggered_by_user or frappe.session.user
            }).insert(ignore_permissions=True)
        except:
            pass

        return {"success": True, "po_name": po.name, "po_custom_name": po_custom_name}

    except Exception as e:
        tracker.fail(f"ERPNext PO for {item_code}", str(e)[:200])
        frappe.log_error(title=f"DDécor PO Creation Failed - {item_code}", message=str(e))
        return {"success": False, "error": str(e)[:200]}


def verify_item_expected(tracker, work_order_name, item_code):
    """Check if the WO item status changed to EXPECTED after PO submit."""
    tracker.step(f"Verifying status → EXPECTED for {item_code}")

    time.sleep(1)

    expected_statuses = ["EXPECTED", "COM_EXPECTED", "TEMPLATE_EXPECTED", "IN_STOCK"]
    status = None

    # Check main required_items
    status = frappe.db.get_value("Work Order Item",
        {"parent": work_order_name, "item_code": item_code},
        "custom_item_ingredient_status")

    # Check additional materials
    if not status or status not in expected_statuses:
        addl_status = frappe.db.get_value("Additional Materials Used",
            {"parent": work_order_name, "item_code": item_code},
            "custom_item_ingredient_status")
        if addl_status:
            status = addl_status

    if status and status in expected_statuses:
        tracker.done()
        tracker.step(f"Verified: {item_code} → {status}")
        tracker.done()
        return {"verified": True, "status": status}

    # Force recalculate and check again
    try:
        frappe.call("recalculate_work_order_ingredient_status",
            work_order_name=work_order_name, force_recalculate=1)
        frappe.db.commit()
        time.sleep(1)

        status = frappe.db.get_value("Work Order Item",
            {"parent": work_order_name, "item_code": item_code},
            "custom_item_ingredient_status")

        if not status or status not in expected_statuses:
            status = frappe.db.get_value("Additional Materials Used",
                {"parent": work_order_name, "item_code": item_code},
                "custom_item_ingredient_status")

        if status and status in expected_statuses:
            tracker.done()
            tracker.step(f"Verified: {item_code} → {status} (after recalc)")
            tracker.done()
            return {"verified": True, "status": status}
    except:
        pass

    tracker.fail(f"Verify {item_code}", f"Status is {status or 'unknown'}, expected EXPECTED")
    return {"verified": False, "status": status or "unknown"}


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def place_order(apo_name):
    """
    Background job. For each item:
    A. Open New Order form on portal
    B. Fill order ref (CCP order ID)
    C. Select Collection → Serial → Enter qty
    D. Submit on portal → read PO-503190 (or Cancel in dry run)
    E. Create ERPNext PO (one item, linked to WO, source flags set)
    F. Submit ERPNext PO → DocType event fires automatically
    G. Verify item status → EXPECTED
    H. Log activity on WO and PO
    I. Navigate back → New Order for next item
    """
    tracker = ProgressTracker(apo_name)

    try:
        cfg = get_settings()
        live = cfg["live_mode"]

        apo = frappe.get_doc("Automated Procurement Order", apo_name)
        items = apo.items
        order_ref_base = apo.order_reference or apo.work_order or apo_name
        work_order_name = apo.work_order
        gst_rate = apo.custom_gst_rate or cfg["gst_rate"]
        triggered_by_user = apo.triggered_by_user or frappe.session.user
        tracker.total_items = len(items)
        username, password = get_credentials(cfg)

        # Get WO display name for PO naming + ref extraction
        wo_display_name = ""
        if work_order_name:
            wo_display_name = frappe.db.get_value("Work Order", work_order_name,
                "custom_work_order_name") or work_order_name

        # Extract CCP order ID for portal ref field
        # F-43-38902-21/1-1 → 38902
        portal_ref = extract_ccp_order_id(wo_display_name)

        # Respect max items limit
        if cfg["max_items"] > 0 and len(items) > cfg["max_items"]:
            tracker.fail("Item limit",
                f"Too many items ({len(items)}). Max is {cfg['max_items']}. Adjust in DDecor Settings.")
            frappe.db.set_value("Automated Procurement Order", apo_name, "status", "Failed")
            frappe.db.commit()
            return

        frappe.db.set_value("Automated Procurement Order", apo_name, {
            "status": "Draft",
            "execution_started": datetime.now().isoformat()
        })
        frappe.db.commit()

    except Exception as e:
        tracker.fail("Loading order", str(e)[:200])
        frappe.db.set_value("Automated Procurement Order", apo_name, "status", "Failed")
        frappe.db.commit()
        return

    result = {"success": False, "items_ordered": [], "po_numbers": [], "erp_pos": [], "error": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg["headless"])
        page = browser.new_page()

        try:
            # Login once
            if not do_login(page, tracker, username, password):
                result["error"] = "Login failed"
                browser.close()
                _finalize(apo_name, result, triggered_by_user, work_order_name)
                return

            # Process each item — one portal order per item
            for idx, item_row in enumerate(items):
                collection = item_row.collection or ""
                serial = item_row.serial_number or ""
                qty = item_row.quantity or 1
                source_table = item_row.source_table or "Main"
                item_label = f"{collection} / Sr {serial}"
                tracker.current_item = item_label

                # Use CCP order ID as portal ref, append item index
                order_ref = f"{portal_ref}-{idx+1}" if len(items) > 1 else portal_ref

                # ── A: Open new order form ──
                if not open_new_order_form(page, tracker):
                    result["error"] = f"Could not open order form for {item_label}"
                    break

                # ── B: Fill order ref ──
                fill_ref(page, tracker, order_ref)

                # ── C: Select collection, serial, enter qty ──
                item_result = order_single_item(page, tracker, collection, serial, qty)

                if not item_result:
                    result["error"] = f"Failed on item: {item_label}"
                    try:
                        cancel_current_order(page, tracker)
                    except:
                        pass
                    break

                # ── D: Submit or Cancel on portal ──
                portal_po_number = None

                if live:
                    po_data = submit_and_read_po(page, tracker)
                    portal_po_number = po_data.get("po_number")
                    item_result["portal_po_number"] = portal_po_number
                    item_result["subtotal"] = po_data.get("subtotal")
                    item_result["tax"] = po_data.get("tax")
                    item_result["total_cost"] = po_data.get("total_cost")

                    if not portal_po_number:
                        result["error"] = f"Could not read PO number for {item_label}"
                        result["items_ordered"].append(item_result)
                        break
                else:
                    cancel_current_order(page, tracker)
                    item_result["portal_po_number"] = None

                result["items_ordered"].append(item_result)

                # ── E+F: Create + Submit ERPNext PO (live only) ──
                if live and portal_po_number:
                    erp_result = create_erp_purchase_order(
                        tracker=tracker,
                        item_row=item_row,
                        work_order_name=work_order_name,
                        portal_po_number=portal_po_number,
                        gst_rate=gst_rate,
                        wo_display_name=wo_display_name,
                        cfg=cfg,
                        source_table=source_table,
                        triggered_by_user=triggered_by_user
                    )

                    if erp_result.get("success"):
                        result["erp_pos"].append(erp_result["po_name"])
                        result["po_numbers"].append(portal_po_number)

                        # Update APO item row
                        try:
                            frappe.db.set_value("Automated Procurement Order Item",
                                item_row.name, {
                                    "portal_po_number": portal_po_number,
                                    "item_status": "Ordered"
                                }, update_modified=False)
                            frappe.db.commit()
                        except:
                            pass

                        # ── G: Verify status → EXPECTED ──
                        verify = verify_item_expected(tracker, work_order_name, item_row.item_code)
                        item_result["erp_po"] = erp_result["po_name"]
                        item_result["verified_status"] = verify.get("status", "unknown")
                        item_result["verified"] = verify.get("verified", False)

                        if not verify.get("verified"):
                            result["error"] = f"Status not EXPECTED for {item_label}: {verify.get('status')}"
                            break
                    else:
                        result["error"] = f"ERPNext PO failed for {item_label}: {erp_result.get('error')}"
                        break

                tracker.item_done(item_label)

                # Pause between items
                time.sleep(cfg["between_delay"])

            # Check if all items completed
            if not result["error"] and len(result["items_ordered"]) == len(items):
                result["success"] = True
                for s in tracker.steps:
                    if s["status"] == "active":
                        s["status"] = "done"
                tracker._push()

        except Exception as e:
            tracker.fail("Unexpected error", str(e)[:200])
            result["error"] = str(e)[:200]
            # keep the FULL text — the 200-char field drops the part of a
            # Playwright error that says WHY a click or wait failed
            try:
                import traceback as _tb
                frappe.log_error(
                    title=f"DDécor order failed — {apo_name}",
                    message=f"{apo_name}\n\n{str(e)}\n\n{_tb.format_exc()}"
                )
            except Exception:
                pass

        finally:
            try:
                browser.close()
            except:
                pass

    _finalize(apo_name, result, triggered_by_user, work_order_name)


def _finalize(apo_name, result, triggered_by_user="", work_order_name=""):
    """Update APO doc with final status. Update DDecor Settings. Log summary on WO."""
    try:
        status = "Ordered" if result["success"] else "Failed"
        update = {
            "status": status,
            "execution_completed": datetime.now().isoformat()
        }

        if result.get("error"):
            update["last_error"] = result["error"]

        if result.get("items_ordered"):
            update["custom_order_result"] = json.dumps(result["items_ordered"])

        po_numbers = result.get("po_numbers", [])
        if po_numbers:
            update["custom_portal_order_number"] = ", ".join(po_numbers)

        erp_pos = result.get("erp_pos", [])
        if erp_pos:
            update["purchase_order"] = erp_pos[0]

        frappe.db.set_value("Automated Procurement Order", apo_name, update, update_modified=True)
        frappe.db.commit()

        # Update DDecor Settings with last run info
        try:
            frappe.db.set_value("DDecor Settings", "DDecor Settings", {
                "last_run_time": datetime.now().isoformat(),
                "last_run_status": status,
                "last_run_items": len(result.get("items_ordered", [])),
                "last_run_apo": apo_name,
                "last_run_error": result.get("error", ""),
                "total_orders_placed": (
                    frappe.db.get_value("DDecor Settings", "DDecor Settings", "total_orders_placed") or 0
                ) + len(erp_pos)
            }, update_modified=False)
            frappe.db.commit()
        except:
            pass

        # Log summary comment on WO
        if work_order_name and erp_pos:
            try:
                user_label = triggered_by_user.split("@")[0] if triggered_by_user else "system"
                items_ordered = result.get("items_ordered", [])
                item_lines = ""
                for io in items_ordered:
                    item_lines += (
                        f"• {io.get('collection', '')} / Sr {io.get('serial_number', '')} "
                        f"— {io.get('qty', '')}m"
                    )
                    if io.get("portal_po_number"):
                        item_lines += f" (DDécor: {io['portal_po_number']})"
                    if io.get("erp_po"):
                        item_lines += f" → <a href='/app/purchase-order/{io['erp_po']}'>{io['erp_po']}</a>"
                    item_lines += "<br>"

                summary = (
                    f"<b>📦 DDécor Batch Order Complete</b><br>"
                    f"Placed by: <b>{user_label}</b><br>"
                    f"APO: <a href='/app/automated-procurement-order/{apo_name}'>{apo_name}</a><br>"
                    f"Items ordered: {len(erp_pos)}<br>"
                    f"{item_lines}"
                    f"Status: <b>{status}</b>"
                )
                if result.get("error"):
                    summary += f"<br>Error: {result['error']}"

                frappe.get_doc({
                    "doctype": "Comment",
                    "comment_type": "Info",
                    "reference_doctype": "Work Order",
                    "reference_name": work_order_name,
                    "content": summary,
                    "comment_email": triggered_by_user or frappe.session.user
                }).insert(ignore_permissions=True)
            except:
                pass

        frappe.publish_realtime(
            event="ddecor_order_complete",
            message={
                "apo_name": apo_name,
                "success": result["success"],
                "error": result.get("error"),
                "po_numbers": po_numbers,
                "erp_pos": erp_pos
            }
        )
    except Exception:
        pass
