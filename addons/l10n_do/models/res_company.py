from odoo import models, fields


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_do_ecf_issuer = fields.Boolean(
        "Is e-CF issuer",
        help="When activating this field, NCF issuance is disabled.",
    )

    def _localization_use_documents(self):
        """ Dominican localization uses documents """
        self.ensure_one()
        return (
            True
            if self.country_id == self.env.ref("base.do")
            else super()._localization_use_documents()
        )
