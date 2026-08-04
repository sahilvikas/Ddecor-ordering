"""
Run in bench console to create DDécor Settings single doctype.
bench --site erp.cozycornerpatios.com console
exec(open('/home/frappe/frappe-bench/apps/ddecor_ordering/setup_settings.py').read())
"""

if frappe.db.exists("DocType", "DDécor Settings"):
    print("DDécor Settings already exists — checking for missing fields...")
    existing = [f.fieldname for f in frappe.get_meta("DDécor Settings").fields]
else:
    print("Creating DDécor Settings doctype...")
    existing = []

fields = [
    # ── Order Placement ──
    {"fieldname": "ordering_section", "fieldtype": "Section Break", "label": "Order Placement"},
    {"fieldname": "live_mode", "fieldtype": "Check", "label": "Live Mode (Place Real Orders)",
     "default": "0", "description": "When OFF: clicks Cancel on DDécor portal (dry run). When ON: clicks Submit and creates ERPNext Purchase Orders."},
    {"fieldname": "auto_submit_erp_po", "fieldtype": "Check", "label": "Auto-Submit ERPNext PO",
     "default": "1", "description": "When ON: automatically submits the ERPNext Purchase Order after creation. When OFF: creates as Draft."},
    {"fieldname": "col_ordering_1", "fieldtype": "Column Break"},
    {"fieldname": "default_gst_rate", "fieldtype": "Select", "label": "Default GST Rate",
     "options": "5\n12\n18", "default": "5"},
    {"fieldname": "headless_browser", "fieldtype": "Check", "label": "Headless Browser",
     "default": "1", "description": "Run browser without visible window. Uncheck for debugging."},
    {"fieldname": "max_items_per_run", "fieldtype": "Int", "label": "Max Items Per Run",
     "default": "20", "description": "Safety limit. Set to 0 for unlimited."},

    # ── Portal Credentials ──
    {"fieldname": "credentials_section", "fieldtype": "Section Break", "label": "DDécor Portal Credentials"},
    {"fieldname": "portal_username", "fieldtype": "Data", "label": "Portal Username",
     "description": "DDécor merchant portal login email"},
    {"fieldname": "col_cred_1", "fieldtype": "Column Break"},
    {"fieldname": "portal_password", "fieldtype": "Password", "label": "Portal Password"},

    # ── Supplier Config ──
    {"fieldname": "supplier_section", "fieldtype": "Section Break", "label": "Supplier Configuration"},
    {"fieldname": "erp_supplier", "fieldtype": "Link", "label": "ERPNext Supplier",
     "options": "Supplier", "default": "Home Ideas",
     "description": "Supplier used when creating Purchase Orders in ERPNext"},
    {"fieldname": "col_sup_1", "fieldtype": "Column Break"},
    {"fieldname": "default_warehouse", "fieldtype": "Link", "label": "Default Warehouse",
     "options": "Warehouse", "default": "Main Warehouse - FAM"},
    {"fieldname": "company", "fieldtype": "Link", "label": "Company",
     "options": "Company", "default": "Fabrics And More"},

    # ── Timeouts ──
    {"fieldname": "timeouts_section", "fieldtype": "Section Break", "label": "Timeouts & Retries", "collapsible": 1},
    {"fieldname": "login_timeout", "fieldtype": "Int", "label": "Login Timeout (seconds)",
     "default": "30", "description": "Max wait for portal login"},
    {"fieldname": "page_load_timeout", "fieldtype": "Int", "label": "Page Load Timeout (seconds)",
     "default": "20", "description": "Max wait for page elements to render"},
    {"fieldname": "col_timeout_1", "fieldtype": "Column Break"},
    {"fieldname": "dropdown_settle_time", "fieldtype": "Float", "label": "Dropdown Settle Time (seconds)",
     "default": "3", "description": "Wait time after selecting collection/serial for table to render"},
    {"fieldname": "between_items_delay", "fieldtype": "Float", "label": "Between Items Delay (seconds)",
     "default": "1", "description": "Pause between processing consecutive items"},

    # ── Notifications ──
    {"fieldname": "notifications_section", "fieldtype": "Section Break", "label": "Notifications", "collapsible": 1},
    {"fieldname": "notify_on_success", "fieldtype": "Check", "label": "Notify on Success", "default": "1"},
    {"fieldname": "notify_on_failure", "fieldtype": "Check", "label": "Notify on Failure", "default": "1"},
    {"fieldname": "col_notif_1", "fieldtype": "Column Break"},
    {"fieldname": "notification_emails", "fieldtype": "Small Text", "label": "Notification Emails",
     "description": "Comma-separated emails to notify on order completion/failure"},

    # ── Status ──
    {"fieldname": "status_section", "fieldtype": "Section Break", "label": "Last Run Status"},
    {"fieldname": "last_run_time", "fieldtype": "Datetime", "label": "Last Run Time", "read_only": 1},
    {"fieldname": "last_run_status", "fieldtype": "Data", "label": "Last Run Status", "read_only": 1},
    {"fieldname": "last_run_items", "fieldtype": "Int", "label": "Last Run Items", "read_only": 1},
    {"fieldname": "col_status_1", "fieldtype": "Column Break"},
    {"fieldname": "last_run_apo", "fieldtype": "Link", "label": "Last Run APO",
     "options": "Automated Procurement Order", "read_only": 1},
    {"fieldname": "last_run_error", "fieldtype": "Small Text", "label": "Last Run Error", "read_only": 1},
    {"fieldname": "total_orders_placed", "fieldtype": "Int", "label": "Total Orders Placed", "read_only": 1, "default": "0"},
]

if not frappe.db.exists("DocType", "DDécor Settings"):
    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": "DDécor Settings",
        "module": "DDécor Ordering",
        "custom": 1,
        "issingle": 1,
        "fields": fields,
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1},
            {"role": "Manufacturing Manager", "read": 1, "write": 1},
        ]
    })
    doc.insert(ignore_permissions=True)
    print("✓ Created DDécor Settings")
else:
    # Add missing fields
    added = 0
    last_field = existing[-1] if existing else ""
    for f in fields:
        fn = f.get("fieldname", "")
        if fn and fn not in existing:
            cf = frappe.get_doc({
                "doctype": "Custom Field",
                "dt": "DDécor Settings",
                "fieldname": fn,
                "fieldtype": f["fieldtype"],
                "label": f.get("label", ""),
                "options": f.get("options", ""),
                "default": f.get("default", ""),
                "description": f.get("description", ""),
                "read_only": f.get("read_only", 0),
                "collapsible": f.get("collapsible", 0),
                "insert_after": last_field
            })
            cf.insert(ignore_permissions=True)
            added += 1
            last_field = fn
    if added:
        print(f"  + Added {added} missing fields")
    else:
        print("  All fields present")

# Set defaults if empty
frappe.db.commit()

try:
    settings = frappe.get_single("DDécor Settings")
    if not settings.portal_username:
        settings.portal_username = "cozycornerpatios@gmail.com"
        settings.portal_password = "Fam@12345678"
        settings.save(ignore_permissions=True)
        print("  Set default credentials")
    if not settings.erp_supplier:
        settings.erp_supplier = "Home Ideas"
        settings.save(ignore_permissions=True)
except:
    pass

frappe.db.commit()
print("\n✓ DDécor Settings ready — go to /app/ddecor-settings to configure")
