import re
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_do_ncf_expiration_date = fields.Date(
        string="Valid until",
    )

    @api.onchange("l10n_latam_document_type_id", "l10n_latam_document_number")
    def _inverse_l10n_latam_document_number(self):
        do_invoices_with_document_number = self.filtered(
            lambda x: x.l10n_latam_document_type_id
            and x.country_code == "DO"
            and x.l10n_latam_use_documents
            and x.l10n_latam_document_number
        )
        for rec in do_invoices_with_document_number:
            l10n_latam_document_number = (
                rec.l10n_latam_document_type_id._format_document_number(
                    rec.l10n_latam_document_number
                )
            )
            if rec.l10n_latam_document_number != l10n_latam_document_number:
                rec.l10n_latam_document_number = l10n_latam_document_number
            rec.name = l10n_latam_document_number

        super(
            AccountMove, self - do_invoices_with_document_number
        )._inverse_l10n_latam_document_number()

    def _get_l10n_latam_documents_domain(self):
        self.ensure_one()
        if not (
            self.journal_id.l10n_latam_use_documents
            and self.journal_id.company_id.country_id == self.env.ref("base.do")
        ):
            return super()._get_l10n_latam_documents_domain()

        internal_types = ["debit_note"]
        if self.move_type in ["out_refund", "in_refund"]:
            internal_types.append("credit_note")
        else:
            internal_types.append("invoice")

        domain = [
            ("internal_type", "in", internal_types),
            ("country_id", "=", self.company_id.country_id.id),
        ]
        ncf_types = self.journal_id._get_journal_ncf_types(
            counterpart_partner=self.partner_id.commercial_partner_id, invoice=self
        )
        domain += [
            "|",
            ("l10n_do_ncf_type", "=", False),
            ("l10n_do_ncf_type", "in", ncf_types),
        ]
        codes = self.journal_id._get_journal_codes()
        if codes:
            domain.append(("code", "in", codes))
        return domain

    def _l10n_do_get_formatted_sequence(self):
        self.ensure_one()
        document_type_id = self.l10n_latam_document_type_id
        return "%s%s" % (
            document_type_id.doc_code_prefix,
            "".zfill(
                10 if str(document_type_id.l10n_do_ncf_type).startswith("e-") else 8
            ),
        )

    def _get_starting_sequence(self):
        if (
            self.l10n_latam_use_documents
            and self.country_code == "DO"
            and self.l10n_latam_document_type_id
        ):
            return self._l10n_do_get_formatted_sequence()

        return super()._get_starting_sequence()

    def _is_l10n_do_manual_document_number(self):
        self.ensure_one()

        if self.reversed_entry_id:
            return self.reversed_entry_id.l10n_latam_manual_document_number

        return self.move_type in (
            "in_invoice",
            "in_refund",
        ) and self.l10n_latam_document_type_id.l10n_do_ncf_type not in (
            "minor",
            "e-minor",
            "informal",
            "e-informal",
            "exterior",
            "e-exterior",
        )

    def _is_manual_document_number(self):
        if self.country_code == "DO":
            return self._is_l10n_do_manual_document_number()
        return super()._is_manual_document_number()

    def _must_check_constrains_date_sequence(self):
        if self.country_code == "DO":
            return False
        return super(AccountMove, self)._must_check_constrains_date_sequence()

    def _get_sequence_format_param(self, previous):

        if self.country_code != "DO":
            return super(AccountMove, self)._get_sequence_format_param(previous)

        regex = r"^(?P<prefix1>.*?)(?P<seq>\d{0,8})$"

        format_values = re.match(regex, previous).groupdict()
        format_values["seq_length"] = len(format_values["seq"])
        format_values["seq"] = int(format_values.get("seq") or 0)
        format_values["year_length"] = 1

        placeholders = re.findall(r"(prefix\d|seq\d?)", regex)
        format = "".join(
            "{seq:0{seq_length}d}" if s == "seq" else "{%s}" % s for s in placeholders
        )
        return format, format_values

    def _get_last_sequence_domain(self, relaxed=False):
        where_string, param = super(AccountMove, self)._get_last_sequence_domain(
            relaxed
        )
        if self.country_code == "DO" and self.l10n_latam_use_documents:
            where_string = where_string.replace("journal_id = %(journal_id)s AND", "")
            where_string += (
                " AND l10n_latam_document_type_id = %(l10n_latam_document_type_id)s AND "
                "company_id = %(company_id)s AND move_type IN %(move_type)s"
            )

            param["company_id"] = self.company_id.id or False
            param["l10n_latam_document_type_id"] = (
                self.l10n_latam_document_type_id.id or 0
            )
            param["move_type"] = (
                ("in_invoice", "in_refund")
                if self.l10n_latam_document_type_id._is_l10n_do_doc_type_vendor()
                else ("out_invoice", "out_refund")
            )
        return where_string, param

    def _get_l10n_do_amounts(self, company_currency=False):
        """
        Method used to to prepare dominican fiscal invoices amounts data. Widely used
        on reports and electronic invoicing.

        Returned values:

        itbis_amount: Total ITBIS
        itbis_taxable_amount: Monto Gravado Total (con ITBIS)
        itbis_exempt_amount: Monto Exento
        """
        self.ensure_one()
        amount_field = company_currency and "balance" or "price_subtotal"
        sign = -1 if (company_currency and self.is_inbound()) else 1

        itbis_tax_group = self.env.ref("l10n_do.group_itbis", False)

        taxed_move_lines = self.line_ids.filtered("tax_line_id")
        itbis_taxed_move_lines = taxed_move_lines.filtered(
            lambda l: itbis_tax_group in l.tax_line_id.mapped("tax_group_id")
            and l.tax_line_id.amount > 0
        )

        itbis_taxed_product_lines = self.invoice_line_ids.filtered(
            lambda l: itbis_tax_group in l.tax_ids.mapped("tax_group_id")
        )

        return {
            "itbis_amount": sign * sum(itbis_taxed_move_lines.mapped(amount_field)),
            "itbis_taxable_amount": sign
            * sum(
                line[amount_field]
                for line in itbis_taxed_product_lines
                if line.price_total != line.price_subtotal
            ),
            "itbis_exempt_amount": sign
            * sum(
                line[amount_field]
                for line in itbis_taxed_product_lines
                if any(True for tax in line.tax_ids if tax.amount == 0)
            ),
            "company_invoice_total": abs(self.amount_untaxed_signed)
            + sum(
                (
                    line.debit or line.credit
                    if self.currency_id == self.company_id.currency_id
                    else abs(line.amount_currency)
                )
                for line in self.line_ids.filtered(
                    lambda l: l.tax_line_id and l.tax_line_id.amount > 0
                )
            ),
            "invoice_total": abs(self.amount_untaxed)
            + sum(
                (
                    line.debit or line.credit
                    if self.currency_id == self.company_id.currency_id
                    else abs(line.amount_currency)
                )
                for line in self.line_ids.filtered(
                    lambda l: l.tax_line_id and l.tax_line_id.amount > 0
                )
            ),
        }

    def _get_name_invoice_report(self):
        self.ensure_one()
        if self.l10n_latam_use_documents and self.country_code == "DO":
            return "l10n_do.report_invoice_document_inherited"
        return super()._get_name_invoice_report()

    @api.constrains("move_type", "l10n_latam_document_type_id")
    def _check_invoice_type_document_type(self):
        l10n_do_invoices = self.filtered(
            lambda inv: inv.country_code == "DO"
            and inv.l10n_latam_use_documents
            and inv.l10n_latam_document_type_id
        )
        for rec in l10n_do_invoices:
            has_vat = bool(rec.partner_id.vat and bool(rec.partner_id.vat.strip()))
            if not has_vat and (
                rec.amount_untaxed_signed >= 250000
                and rec.commercial_partner_id.l10n_do_dgii_tax_payer_type == "non_payer"
            ):
                raise ValidationError(
                    _(
                        "A VAT is mandatory for this type of NCF. "
                        "Please set the current VAT of this client"
                    )
                )
        super(AccountMove, self - l10n_do_invoices)._check_invoice_type_document_type()

    def _post(self, soft=True):

        res = super()._post(soft)

        l10n_do_invoices = self.filtered(
            lambda inv: inv.country_code == "DO"
            and inv.l10n_latam_use_documents
            and inv.l10n_latam_document_type_id
        )

        for invoice in l10n_do_invoices:
            invoice.l10n_do_ncf_expiration_date = (
                invoice.journal_id.l10n_do_document_type_ids.filtered(
                    lambda doc: doc.l10n_latam_document_type_id
                    == invoice.l10n_latam_document_type_id
                ).l10n_do_ncf_expiration_date
            )

        non_payer_type_invoices = l10n_do_invoices.filtered(
            lambda inv: not inv.partner_id.l10n_do_dgii_tax_payer_type
        )
        if non_payer_type_invoices:
            raise ValidationError(_("Fiscal invoices require partner fiscal type"))

        return res

    def unlink(self):
        if self.filtered(
            lambda inv: inv.is_purchase_document()
            and inv.country_code == "DO"
            and inv.l10n_latam_use_documents
            and inv.posted_before
        ):
            raise UserError(
                _("You cannot delete fiscal invoice which have been posted before")
            )
        return super(AccountMove, self).unlink()
