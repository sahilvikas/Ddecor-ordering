"""
Run in bench console to set up the DDécor Ordering app.
Creates the Automated Procurement Order doctype + fields if they don't exist.
"""

# ── 1. Check if APO doctype exists, create if not ──
if not frappe.db.exists("DocType", "Automated Procurement Order"):
    print("Creating Automated Procurement Order doctype...")

    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": "Automated Procurement Order",
        "module": "DDécor Ordering",
        "custom": 1,
        "autoname": "APO-.#####",
        "is_submittable": 0,
        "fields": [
            {"fieldname": "supplier", "fieldtype": "Link", "label": "Supplier", "options": "Supplier", "in_list_view": 1},
            {"fieldname": "work_order", "fieldtype": "Link", "label": "Work Order", "options": "Work Order"},
            {"fieldname": "channel", "fieldtype": "Select", "label": "Channel", "options": "\nSupplier Portal\nWhatsApp", "in_list_view": 1},
            {"fieldname": "status", "fieldtype": "Select", "label": "Status", "options": "\nDraft\nOrdered\nConfirmed\nPartially Received\nReceived\nFailed\nCancelled", "in_list_view": 1, "default": "Draft"},
            {"fieldname": "order_date", "fieldtype": "Datetime", "label": "Order Date", "in_list_view": 1},
            {"fieldname": "col_break_1", "fieldtype": "Column Break"},
            {"fieldname": "custom_order_ref", "fieldtype": "Data", "label": "Order Reference"},
            {"fieldname": "custom_gst_rate", "fieldtype": "Int", "label": "GST Rate %", "default": 5},
            {"fieldname": "custom_portal_order_number", "fieldtype": "Data", "label": "Portal Order Number"},
            {"fieldname": "custom_portal_invoice_number", "fieldtype": "Data", "label": "Portal Invoice Number"},
            {"fieldname": "supplier_response", "fieldtype": "Small Text", "label": "Supplier Response"},
            {"fieldname": "sec_progress", "fieldtype": "Section Break", "label": "Progress"},
            {"fieldname": "custom_progress", "fieldtype": "Long Text", "label": "Progress JSON"},
            {"fieldname": "custom_error", "fieldtype": "Long Text", "label": "Error"},
            {"fieldname": "custom_order_result", "fieldtype": "Long Text", "label": "Order Result JSON"},
            {"fieldname": "sec_items", "fieldtype": "Section Break", "label": "Items"},
            {"fieldname": "items", "fieldtype": "Table", "label": "Items", "options": "Automated Procurement Order Item"},
        ],
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            {"role": "Manufacturing Manager", "read": 1, "write": 1, "create": 1},
            {"role": "Manufacturing User", "read": 1, "write": 1, "create": 1},
        ]
    })
    doc.insert(ignore_permissions=True)
    print("  ✓ Created Automated Procurement Order")
else:
    print("  Automated Procurement Order already exists")

    # Add missing custom fields
    existing_fields = [f.fieldname for f in frappe.get_meta("Automated Procurement Order").fields]
    new_fields = {
        "custom_order_ref": {"fieldtype": "Data", "label": "Order Reference"},
        "custom_gst_rate": {"fieldtype": "Int", "label": "GST Rate %", "default": "5"},
        "custom_portal_order_number": {"fieldtype": "Data", "label": "Portal Order Number"},
        "custom_portal_invoice_number": {"fieldtype": "Data", "label": "Portal Invoice Number"},
        "custom_progress": {"fieldtype": "Long Text", "label": "Progress JSON"},
        "custom_error": {"fieldtype": "Long Text", "label": "Error"},
        "custom_order_result": {"fieldtype": "Long Text", "label": "Order Result JSON"},
    }
    for fname, fdef in new_fields.items():
        if fname not in existing_fields:
            frappe.get_doc({
                "doctype": "Custom Field",
                "dt": "Automated Procurement Order",
                "fieldname": fname,
                "fieldtype": fdef["fieldtype"],
                "label": fdef["label"],
                "insert_after": existing_fields[-1] if existing_fields else ""
            }).insert(ignore_permissions=True)
            print(f"  + Added field: {fname}")


# ── 2. Check if APO Item child table exists ──
if not frappe.db.exists("DocType", "Automated Procurement Order Item"):
    print("Creating Automated Procurement Order Item doctype...")

    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": "Automated Procurement Order Item",
        "module": "DDécor Ordering",
        "custom": 1,
        "istable": 1,
        "fields": [
            {"fieldname": "item_code", "fieldtype": "Link", "label": "Item Code", "options": "Item", "in_list_view": 1},
            {"fieldname": "item_name", "fieldtype": "Data", "label": "Item Name", "in_list_view": 1},
            {"fieldname": "quantity", "fieldtype": "Float", "label": "Quantity", "in_list_view": 1},
            {"fieldname": "rate", "fieldtype": "Currency", "label": "Rate", "in_list_view": 1},
            {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount", "in_list_view": 1},
            {"fieldname": "col_break_1", "fieldtype": "Column Break"},
            {"fieldname": "custom_ddecor_collection", "fieldtype": "Data", "label": "DDécor Collection"},
            {"fieldname": "custom_ddecor_serial", "fieldtype": "Data", "label": "DDécor Serial"},
            {"fieldname": "source_warehouse", "fieldtype": "Link", "label": "Source Warehouse", "options": "Warehouse"},
            {"fieldname": "work_order", "fieldtype": "Link", "label": "Work Order", "options": "Work Order"},
        ]
    })
    doc.insert(ignore_permissions=True)
    print("  ✓ Created Automated Procurement Order Item")
else:
    print("  Automated Procurement Order Item already exists")

    # Add missing fields
    existing_fields = [f.fieldname for f in frappe.get_meta("Automated Procurement Order Item").fields]
    for fname, fdef in {
        "custom_ddecor_collection": {"fieldtype": "Data", "label": "DDécor Collection"},
        "custom_ddecor_serial": {"fieldtype": "Data", "label": "DDécor Serial"},
    }.items():
        if fname not in existing_fields:
            frappe.get_doc({
                "doctype": "Custom Field",
                "dt": "Automated Procurement Order Item",
                "fieldname": fname,
                "fieldtype": fdef["fieldtype"],
                "label": fdef["label"],
                "insert_after": existing_fields[-1] if existing_fields else ""
            }).insert(ignore_permissions=True)
            print(f"  + Added field: {fname}")


# ── 3. Add DDécor credentials to site config ──
if not frappe.conf.get("ddecor_username"):
    print("\n  ⚠ DDécor credentials not in site_config.json")
    print("  Run on server:")
    print('    bench --site erp.cozycornerpatios.com set-config ddecor_username "cozycornerpatios@gmail.com"')
    print('    bench --site erp.cozycornerpatios.com set-config ddecor_password "YOUR_PASSWORD"')
else:
    print(f"  ✓ DDécor credentials configured: {frappe.conf.ddecor_username}")


frappe.db.commit()
print("\n  Setup complete!")
