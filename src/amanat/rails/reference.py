"""Reference rails: what card networks, PSPs and payment protocols say about a hold.

These have no adapter in this repository and the policy engine does not plan around them. They
exist so the registry can be *compared*: for each rail, does authorisation hold funds, can a
capture be smaller, can the rest be released, how long does a hold last, and does a retry act
once. Every row carries a verbatim quote from the source it cites; `python -m amanat.registry.watch`
re-checks each one against its page, and a row it cannot find is a defect in the row.

Rows were proposed by agents reading the sources and admitted by that check, not by the agents'
say-so. Interpretation is a separate matter: where a source is silent, or two readings are
possible, the row is UNVERIFIED (refused, never assumed) and its notes say what was read.

Read 20 Sep 2026. A page that changes will be reported by the next watch run.
"""
from __future__ import annotations

from amanat.rails.semantics import RAILS, Capability, Limit, RailProfile, SourceTier

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------
_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS = (
    'https://usa.visa.com/content/dam/VCOM/regional/na/us/support-legal/documents/authorization-and-reversal-processing-best-practices-for-merchants.pdf'
)
_STRIPE_PLACE_A_HOLD_ON_A_PAYMENT_METHOD = (
    'https://docs.stripe.com/payments/place-a-hold-on-a-payment-method.md'
)
_STRIPE_CAPTURE = (
    'https://docs.stripe.com/api/payment_intents/capture.md'
)
_STRIPE_MULTICAPTURE_VARIANT = (
    'https://docs.stripe.com/payments/multicapture.md?platform=web&ui=stripe-hosted'
)
_STRIPE_OVERCAPTURE_VARIANT = (
    'https://docs.stripe.com/payments/overcapture.md?platform=web&ui=stripe-hosted'
)
_STRIPE_LIFECYCLE = (
    'https://docs.stripe.com/payments/paymentintents/lifecycle.md'
)
_STRIPE_INCREMENTAL_AUTHORIZATION_VARIANT = (
    'https://docs.stripe.com/payments/incremental-authorization.md?platform=web&ui=stripe-hosted'
)
_STRIPE_IDEMPOTENT_REQUESTS = (
    'https://docs.stripe.com/api/idempotent_requests.md'
)
_STRIPE_EXTENDED_AUTHORIZATION_VARIANT = (
    'https://docs.stripe.com/payments/extended-authorization.md?platform=web&ui=stripe-hosted'
)
_ADYEN_CAPTURE = (
    'https://docs.adyen.com/online-payments/capture/'
)
_ADYEN_API_IDEMPOTENCY = (
    'https://docs.adyen.com/development-resources/api-idempotency/'
)
_ADYEN_ADJUST_AUTHORISATION = (
    'https://docs.adyen.com/online-payments/adjust-authorisation/'
)
_ADYEN_CANCEL = (
    'https://docs.adyen.com/online-payments/cancel/'
)
_X402_PAYMENT_IDENTIFIER = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/extensions/payment_identifier.md'
)
_X402_SCHEME_EXACT = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/exact/scheme_exact.md'
)
_X402_SCHEME_EXACT_EVM = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/exact/scheme_exact_evm.md'
)
_X402_X402_SPECIFICATION_V2 = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/x402-specification-v2.md'
)
_X402_SCHEME_UPTO_EVM = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/upto/scheme_upto_evm.md'
)
_X402_SCHEME_UPTO = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/upto/scheme_upto.md'
)
_X402_SCHEME_UPTO_SVM = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/upto/scheme_upto_svm.md'
)
_X402_SCHEME_AUTH_CAPTURE = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/auth-capture/scheme_auth_capture.md'
)
_X402_SCHEME_AUTH_CAPTURE_EVM = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/auth-capture/scheme_auth_capture_evm.md'
)
_X402_SCHEME_BATCH_SETTLEMENT_EVM = (
    'https://raw.githubusercontent.com/x402-foundation/x402/c9160a6cbf0fc831ac7036d400ef2d671493e392/specs/schemes/batch-settlement/scheme_batch_settlement_evm.md'
)

VISA_CARD_AUTH = RailProfile(
    rail_id='visa_card_auth',
    display_name='Visa card authorization (merchant requirements guide)',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 1, Efficiently managing authorizations'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'With each successful authorization, the issuer typically reduces the amount available '
                'to the cardholder for other purchases to cover the approved transaction - this is '
                'commonly known as an authorization hold.'
            ),
            notes=(
                "The hold lowers the cardholder's available amount, and the guide says the issuer "
                "'typically' does this, so it is issuer behaviour rather than a network guarantee. Page "
                "1 says an unsettled hold ties up money the cardholder could use elsewhere. Visa's own "
                'merchant guide, which states that the Visa Rules govern in any conflict: SECONDARY '
                'until the Rules are read.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 3, Authorization reversals'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'Completed transaction where the sum of an estimated authorization and any incremental '
                'authorization(s) exceeds the final amount: the difference between the authorized '
                'amount (or amounts) and the transaction amount must be reversed within 24 hours of '
                'when the transaction is completed.'
            ),
            notes=(
                'By necessary implication a completed transaction may be for less than the authorised '
                'sum, with the merchant reversing the difference. The rule is written for estimated and '
                "incremental authorizations, not for every Visa authorization. Visa's own merchant "
                'guide, which states that the Visa Rules govern in any conflict: SECONDARY until the '
                'Rules are read.'
            ),
        ),
        Capability(
            name='partial_void', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 2, Estimated authorization request'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'If the estimated authorization exceeds the final amount, the merchant must reduce the '
                'authorized amount using a partial authorization reversal.'
            ),
            notes=(
                'A partial authorization reversal releases part of a hold, and the guide makes it '
                'mandatory when the estimate exceeds the final amount. Reversals notify the issuer that '
                "the hold should be removed or adjusted (p.3). Visa's own merchant guide, which states "
                'that the Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Capability(
            name='void_whole_hold', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 4, Processing integrity fees, Misuse of '
                'authorization system fee'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'If an authorization was attempted and received but the transaction was not settled, '
                'merchants must reverse the authorization. In order to maintain the data integrity of '
                'the Visa authorization system, a Misuse of Authorization System Fee is assessed by '
                'Visa to approved and partially-approved authorizations that cannot be matched to a '
                'clearing transaction or an authorization reversal.'
            ),
            notes=(
                'Reversing an unused authorization is compulsory, and Visa charges a fee on approved '
                'authorizations that match neither a clearing nor a reversal. The 24-hour timing rule '
                "is on page 3. Visa's own merchant guide, which states that the Visa Rules govern in "
                'any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Capability(
            name='incremental_authorization', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 3, Incremental authorization request'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'A merchant may request an incremental authorization any time the total authorized '
                'amount appears to be insufficient. A merchant may request multiple incremental '
                'authorizations for a single transaction.'
            ),
            notes=(
                'It must follow an estimated or another incremental authorization (p.3), does not '
                'extend the validity window (p.4) and has no stated count limit (p.5). An initial '
                "authorization cannot be incremented (p.6). Visa's own merchant guide, which states "
                'that the Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Capability(
            name='buffered_authorisation', supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 2, Estimated authorization request'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'An estimated authorization must be a genuine estimate and must not be an arbitrary '
                'amount. … Visa requires that an estimated authorization must not contain incidental '
                'spend amounts such as tips or a buffer for damage.'
            ),
            notes=(
                'Second statement of the same rule: an estimated authorization may not carry tips or a '
                'damage buffer, and the stated aim is to prevent over-authorization. It applies to '
                'estimated authorizations; the guide does not extend it to other authorization types. '
                "Visa's own merchant guide, which states that the Visa Rules govern in any conflict: "
                'SECONDARY until the Rules are read.'
            ),
        ),
        Capability(
            name='capped_initial_authorization', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 6, Common Questions, initial authorization'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'The amount of the authorization is capped. The merchant is not permitted to do an '
                'incremental authorization following an initial authorization and must stop the '
                'transaction if the purchase hits the authorized amount.'
            ),
            notes=(
                'New capability name capped_initial_authorization: a fixed capped amount is authorised '
                'before the final amount is known, with no increments and a hard stop at the cap. From '
                'April 2025 only automated fuel dispensers may use it, while other unattended merchants '
                "are encouraged to move to estimated authorizations (p.6). Visa's own merchant guide, "
                'which states that the Visa Rules govern in any conflict: SECONDARY until the Rules are '
                'read.'
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=None,
            source_tier=SourceTier.UNVERIFIED, obtained_on="2026-09-20",
            citation="not established", url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            notes=(
                'Not established. Reading 1: the merchant must send a reversal for the difference '
                "within 24 hours (p.3), so release is not automatic from the merchant's side. Reading "
                '2: issuers match reversals and clearings to the authorization and a failed match '
                'leaves funds held longer, but the guide does not say whether the difference is freed '
                "without a reversal. Visa's own merchant guide, which states that the Visa Rules govern "
                'in any conflict: SECONDARY until the Rules are read. The closest sentence read: “Visa '
                'processing requirements are designed to assist issuers in matching these multiple '
                'authorization messages with the clearing. Missing or non-matching data elements may '
                'mean issuers are not able to affect a match, which often means that funds remain held '
                'for a longer period or in duplicate.”'
            ),
        ),
        Capability(
            name='over_capture', supported=None,
            source_tier=SourceTier.UNVERIFIED, obtained_on="2026-09-20",
            citation="not established", url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            notes=(
                "Not established. The guide's only stated route to a higher final amount is an "
                'incremental authorization; it is silent on whether clearing above the authorised sum '
                'is allowed. Marked null because the absence of a stated permission does not settle the '
                "question. Visa's own merchant guide, which states that the Visa Rules govern in any "
                'conflict: SECONDARY until the Rules are read. The closest sentence read: “If the '
                'cardholder spends more than expected, the merchant may obtain an additional '
                'authorization using an incremental authorization request.”'
            ),
        ),
    ],
    limits=[
        Limit(
            name='hold_expiry_days_lodging_vehicle_rental_cruise', value=30, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 4, Transaction and processing timeframes'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'Lodging, Vehicle Rental, Cruise Line Merchants – 30 days from day of estimated '
                'authorization approval'
            ),
            notes=(
                'Per the page-4 lead-in, the maximum time from a valid estimated authorization to '
                'processing, counted from approval; the Visa Rules govern and country-specific '
                'timeframes apply. Incremental authorizations do not extend it, so a longer stay needs '
                "a reversal and a new authorization. Visa's own merchant guide, which states that the "
                'Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Limit(
            name='hold_expiry_days_rental_merchant_categories', value=10, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 4, Transaction and processing timeframes'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote='Rental Merchant Categories – 10 days from day of estimated authorization approval',
            notes=(
                'Per the page-4 lead-in, the maximum time from a valid estimated authorization to '
                'processing, counted from approval; the Visa Rules govern and country-specific '
                'timeframes apply. Incremental authorizations do not extend it, so a longer stay needs '
                "a reversal and a new authorization. Visa's own merchant guide, which states that the "
                'Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Limit(
            name='hold_expiry_days_card_absent_cardholder_initiated', value=10, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 4, Transaction and processing timeframes'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'Cardholder-initiated Transactions in a Card-Absent Environment – 10 days from day of '
                'estimated authorization approval'
            ),
            notes=(
                'Per the page-4 lead-in, the maximum time from a valid estimated authorization to '
                'processing, counted from approval; the Visa Rules govern and country-specific '
                'timeframes apply. Incremental authorizations do not extend it, so a longer stay needs '
                "a reversal and a new authorization. Visa's own merchant guide, which states that the "
                'Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Limit(
            name='hold_expiry_days_card_present', value=5, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 4, Transaction and processing timeframes'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote='Card-Present Transactions – 5 days from day of estimated authorization approval',
            notes=(
                'Per the page-4 lead-in, the maximum time from a valid estimated authorization to '
                'processing, counted from approval; the Visa Rules govern and country-specific '
                'timeframes apply. Incremental authorizations do not extend it, so a longer stay needs '
                "a reversal and a new authorization. Visa's own merchant guide, which states that the "
                'Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Limit(
            name='reversal_deadline_hours', value=24, unit='hours',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 3, Authorization reversals'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'Transaction not completed: Entire authorized amount must be reversed within 24 hours '
                'of when the merchant becomes aware that that transaction would not be completed or the '
                'end of the authorization validity period.'
            ),
            notes=(
                'The 24-hour clock runs from the merchant learning the transaction will not complete, '
                "or from the end of the validity period. The lead-in says merchants 'should' reverse in "
                "a timely manner while the bullet says 'must'. Visa's own merchant guide, which states "
                'that the Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
        Limit(
            name='reversal_deadline_hours_excess_after_completion', value=24, unit='hours',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Visa, Estimated and Incremental Authorization and Reversal Processing Requirements for '
                'Visa Merchants (PDF, ©2024 Visa), p. 3, Authorization reversals'
            ), url=_VISA_AUTHORIZATION_AND_REVERSAL_PROCESSING_BEST_PRACTICES_FOR_MERCHANTS,
            quote=(
                'Completed transaction where the sum of an estimated authorization and any incremental '
                'authorization(s) exceeds the final amount: the difference between the authorized '
                'amount (or amounts) and the transaction amount must be reversed within 24 hours of '
                'when the transaction is completed.'
            ),
            notes=(
                'Applies when a completed transaction is for less than the authorised sum: the '
                'difference must be reversed within 24 hours of completion. The guide is silent on any '
                "issuer-side release of that difference. Visa's own merchant guide, which states that "
                'the Visa Rules govern in any conflict: SECONDARY until the Rules are read.'
            ),
        ),
    ],
)


STRIPE_CARD_MANUAL_CAPTURE = RailProfile(
    rail_id='stripe_card_manual_capture',
    hyperswitch_connector='stripe',
    display_name='Stripe cards, manual capture (hold, then capture)',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Place a hold on a payment method, introduction', url=_STRIPE_PLACE_A_HOLD_ON_A_PAYMENT_METHOD,
            quote=(
                'When you create a payment, you can place a hold on an eligible payment method to '
                'reserve funds that you can capture later.'
            ),
            notes=(
                'Stripe describes card authorisation as a hold that reserves funds until capture. Only '
                'some payment methods support hold-then-capture: cards do, ACH and iDEAL do not.'
            ),
        ),
        Capability(
            name='payment_guarantee', supported=None,
            source_tier=SourceTier.UNVERIFIED, obtained_on="2026-09-21",
            citation='not established', url=_STRIPE_PLACE_A_HOLD_ON_A_PAYMENT_METHOD,
            notes=(
                'Not established. Stripe\'s sentence, “Authorising a payment guarantees the amount by '
                'holding it on the customer’s payment method.” (the page\'s spelling varies with the '
                'reader\'s locale), is about reserving the amount, not about the merchant being paid. '
                'The same page limits the hold in time (`hold_expiry_days`), and nothing read says a '
                'merchant is paid once an authorisation succeeds; disputes and the card networks\' own '
                'rules on late presentment were not read. This row was SECONDARY and `supported=True` '
                'until a payments review on 21 Sep 2026 pointed out that, beside UPI Reserve Pay\'s '
                'explicit disclaimer (`sbmd.payment_guarantee`), it told a reader the opposite of the '
                'truth: NPCI wrote its disclaimer down, and Stripe used the word "guarantees" in prose. '
                'The difference between the two cells is disclosure, not economics.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe API Reference: Capture a PaymentIntent, Parameters > amount_to_capture', url=_STRIPE_CAPTURE,
            quote=(
                'The amount to capture from the PaymentIntent, which must be less than or equal to the '
                'original amount.'
            ),
            notes=(
                "Capturing less than the authorised amount is allowed, and the hold page's worked "
                'example captures 7.50 USD of an authorised 10.99 USD payment. The same sentence says '
                'less than or equal to the original amount; capturing more is a separate opt-in feature '
                '(overcapture).'
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Place a hold on a payment method, Capture the funds', url=_STRIPE_PLACE_A_HOLD_ON_A_PAYMENT_METHOD,
            quote='A partial capture automatically releases the remaining amount.',
            notes=(
                'Default after a single partial capture. With multicapture (final_capture=false) the '
                'remainder stays authorised until a final capture, an explicit release or expiry. The '
                'sentence says Stripe releases the remaining amount; it does not say when the '
                "cardholder's issuer frees the cardholder-side hold, a separate step the page does not "
                'describe.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Capture a payment multiple times, page summary', url=_STRIPE_MULTICAPTURE_VARIANT,
            quote='Capture a PaymentIntent multiple times, up to the authorised amount.',
            notes=(
                'Opt-in: IC+ pricing, online card payments, capture_method=manual, and multicapture '
                'must show as available on the charge. Stripe allows up to 50 non-final captures plus '
                'one final capture per PaymentIntent.'
            ),
        ),
        Capability(
            name='over_capture', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Capture more than the authorised amount on a payment, introduction', url=_STRIPE_OVERCAPTURE_VARIANT,
            quote=(
                'Overcapture allows you to capture with an amount that’s higher than the authorised '
                'amount for a card payment.'
            ),
            notes=(
                'Opt-in per PaymentIntent (request_overcapture=if_available), offered to IC+ pricing '
                'users on Visa, Mastercard, American Express or Discover, with per-brand and '
                'per-category caps. The API capture reference still says amount_to_capture must be less '
                'than or equal to the original amount, so the two Stripe pages read differently.'
            ),
        ),
        Capability(
            name='void_whole_hold', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Stripe Docs: How Payment Intents and Setup Intents work, Lifecycle table, Cancelled '
                'row'
            ), url=_STRIPE_LIFECYCLE,
            quote=(
                'Cancellation invalidates the PaymentIntent for future payment attempts, releases any '
                'held funds and can’t be undone.'
            ),
            notes=(
                'Cancelling the PaymentIntent releases held funds and cannot be undone; it must happen '
                'before the PaymentIntent reaches processing or succeeded. The cancel API reference '
                'adds that for requires_capture the remaining amount_capturable is automatically '
                'refunded.'
            ),
        ),
        Capability(
            name='incremental_authorization', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Increment an authorisation, introduction', url=_STRIPE_INCREMENTAL_AUTHORIZATION_VARIANT,
            quote=(
                'Incremental authorisation allows you to increase the authorised amount on a confirmed '
                'PaymentIntent before you capture it.'
            ),
            notes=(
                'Only for Visa, Mastercard, American Express and Discover, offered to IC+ pricing '
                'users, and only while the PaymentIntent is completely uncaptured. Maximum of 10 '
                'attempts per PaymentIntent, each increment capped at the higher of 500 USD or 500% '
                'over the previously authorised amount, and increments do not extend the validity '
                'window.'
            ),
        ),
        Capability(
            name='idempotent_replay', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe API Reference: Idempotent requests, introduction', url=_STRIPE_IDEMPOTENT_REQUESTS,
            quote=(
                'Stripe’s idempotency works by saving the resulting status code and body of the first '
                'request made for any given idempotency key, regardless of whether it succeeds or '
                'fails. Subsequent requests with the same key return the same result'
            ),
            notes=(
                'POST requests accept an Idempotency-Key of up to 255 characters, and a replay returns '
                'the stored result including 500 errors. Reusing a key with different parameters '
                'returns an error.'
            ),
        ),
        Capability(
            name='partial_void', supported=None,
            source_tier=SourceTier.UNVERIFIED, obtained_on="2026-09-20",
            citation="not established", url=_STRIPE_MULTICAPTURE_VARIANT,
            notes=(
                "Not established. Closest analogue only: the sentence continues 'to 0 and set "
                "final_capture to true', which releases the whole remainder back to the cardholder "
                'after at least one capture and moves the PaymentIntent to succeeded. None of the '
                'Stripe pages read describes reducing an uncaptured authorisation by a partial amount, '
                'so partial_void stays unverified for Stripe. The closest sentence read: “If you '
                'performed at least one capture and want to release the remaining uncaptured funds, set '
                'the amount to”'
            ),
        ),
    ],
    limits=[
        Limit(
            name='hold_expiry_days', value=7, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation=(
                'Stripe Docs: Place a hold on a payment method, Extended authorisations note (under '
                'Tell Stripe to authorise only)'
            ), url=_STRIPE_PLACE_A_HOLD_ON_A_PAYMENT_METHOD,
            quote='Usually, an authorisation for an online card payment is valid for 7 days.',
            notes=(
                'Default validity for online card payments; in-person Terminal card payments are '
                'usually 2 days and Visa merchant-initiated card-not-present is 5 days (separate row). '
                'On expiry the funds are released and the PaymentIntent status changes to canceled.'
            ),
        ),
        Limit(
            name='hold_expiry_days_extended', value=30, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe Docs: Place an extended hold on an online card payment, introduction', url=_STRIPE_EXTENDED_AUTHORIZATION_VARIANT,
            quote='extended validity periods can go up to 30 days depending on the card network.',
            notes=(
                'Opt-in for online cards (request_extended_authorization=if_available), only on Visa, '
                'Mastercard, American Express and Discover, offered to IC+ pricing users, with '
                'merchant-category limits on some networks. The exact Visa window is 29 days and 18 '
                'hours, and Stripe says to rely on the capture_before field because the rules can '
                'change without notice.'
            ),
        ),
        Limit(
            name='idempotency_key_retention_hours_min', value=24, unit='hours',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Stripe API Reference: Idempotent requests, introduction', url=_STRIPE_IDEMPOTENT_REQUESTS,
            quote='You can remove keys from the system automatically after they’re at least 24 hours old.',
            notes=(
                'Keys may be removed once at least 24 hours old, so 24 hours is a floor and not a '
                'guaranteed maximum. A key reused after pruning generates a new request.'
            ),
        ),
    ],
)


ADYEN_CARD_AUTH = RailProfile(
    rail_id='adyen_card_auth',
    hyperswitch_connector='adyen',
    display_name='Adyen cards, pre-authorization and capture',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Capture, introduction', url=_ADYEN_CAPTURE,
            quote="The authorization reserves the funds on the shopper's bank account.",
            notes=(
                "Applies when separate (manual or delayed) capture is used; Adyen's default is to "
                'capture automatically, immediately after authorization. Pre-authorization is described '
                'as checking sufficient funds without debiting the account.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Capture, Partial manual capture', url=_ADYEN_CAPTURE,
            quote=(
                'Any unclaimed amount that is left over after partially capturing a payment is '
                'automatically cancelled.'
            ),
            notes=(
                'Partial capture is a documented mode, and the capture amount must be the same as or, '
                'for a partial capture, less than the authorized amount. Some payment methods return '
                "'Only possible to capture the full amount', so support is per payment method."
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Capture, Partial manual capture > Single partial capture', url=_ADYEN_CAPTURE,
            quote=(
                'Any unclaimed amount that is left over after partially capturing a payment is '
                'automatically cancelled.'
            ),
            notes=(
                'Holds for the single partial capture type; with multiple partial captures enabled the '
                'leftover is not cancelled automatically. The pages say the leftover is cancelled but '
                'do not say when the issuer frees the shopper-side hold.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Capture, Partial manual capture > Multiple partial captures', url=_ADYEN_CAPTURE,
            quote='The unclaimed amount after an initial partial capture is not automatically cancelled.',
            notes=(
                "Disabled by default; Adyen Support must enable it. The related 'Adjust an "
                "authorization' page adds that the number of partial captures depends on the issuer and "
                'that some issuers may flag multiple partial captures as a fraud risk that can make the '
                'capture fail.'
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: API idempotency, introduction (default accounting rules)', url=_ADYEN_API_IDEMPOTENCY,
            quote=(
                'when partial captures are allowed, it is not possible to capture a higher amount than '
                'the authorized one.'
            ),
            notes=(
                "The capture page's failure reason 'The requested capture amount is more than the "
                "balance on the payment' says the same. No overcapture feature is described on the "
                'pages read.'
            ),
        ),
        Capability(
            name='partial_void', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Authorization adjustment, Authorization type > Pre-authorization', url=_ADYEN_ADJUST_AUTHORISATION,
            quote=(
                'It allows you to increase or decrease the initially authorized amount at a later point '
                'in time.'
            ),
            notes=(
                'Decreasing works only for the pre-authorization type on eligible card schemes and '
                "merchant category codes, and is ultimately up to the issuing bank. The related 'Adjust "
                "an authorization' page adds that a zero-value adjustment is not allowed and that at "
                'most 50 adjustments are allowed per payment.'
            ),
        ),
        Capability(
            name='incremental_authorization', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Authorization adjustment, introduction', url=_ADYEN_ADJUST_AUTHORISATION,
            quote=(
                'Using the authorization type pre-authorization for your payment request, you can '
                'increase or decrease the authorized amount at a later stage, and then capture the '
                'payment manually.'
            ),
            notes=(
                'Same pre-authorization-only mechanism, with eligibility set by card scheme and '
                "merchant category code. The related 'Adjust an authorization' page adds that a "
                'Mastercard amount adjustment automatically extends the validity period.'
            ),
        ),
        Capability(
            name='void_whole_hold', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Cancel, introduction', url=_ADYEN_CANCEL,
            quote=(
                'When you cancel the payment, the financial institution releases the funds back to the '
                "shopper's bank account."
            ),
            notes=(
                'Only before capture: after a payment has been captured it can no longer be cancelled, '
                'and an expired authorization can no longer be cancelled either. Cancelling by your own '
                'reference works only within 24 hours of authorization.'
            ),
        ),
        Capability(
            name='idempotent_replay', supported=True,
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: API idempotency, Enable idempotency', url=_ADYEN_API_IDEMPOTENCY,
            quote=(
                'If the Adyen payments platform already processed the request, the response to the '
                'first attempt will be returned without duplication.'
            ),
            notes=(
                'Send an idempotency-key header on POST requests; keys are at most 64 characters. A '
                'duplicate sent while the first is still running returns HTTP 422 or 409 with error '
                'code 704, and a transient-error header marks retryable failures.'
            ),
        ),
    ],
    limits=[
        Limit(
            name='hold_expiry_days', value=28, unit='days',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: Authorization adjustment, Expiration of pre-authorizations', url=_ADYEN_ADJUST_AUTHORISATION,
            quote=(
                'For the global card schemes, Adyen expires (pre-)authorization requests automatically '
                'after 28 days from the day the payment is authorized.'
            ),
            notes=(
                "Adyen's own expiry, layered on the card scheme's validity period and changeable "
                'through Adyen Support per merchant account and scheme. Manual capture after the '
                'scheme-side expiry is possible but raises the risk of a failed capture, extra fees and '
                "'No Authorization' or 'Late Presentment' chargebacks; after Adyen's own expiry the "
                'payment can no longer be captured or cancelled.'
            ),
        ),
        Limit(
            name='idempotency_key_retention_hours_min', value=168, unit='hours',
            source_tier=SourceTier.SECONDARY, obtained_on="2026-09-20",
            citation='Adyen Docs: API idempotency, Key scope and validity time', url=_ADYEN_API_IDEMPOTENCY,
            quote='Idempotency keys are valid for a period of 7 to 14 days after first submission.',
            notes=(
                'Adyen states a range of 7 to 14 days (168 to 336 hours), so the value is the lower '
                'bound. Keys are stored per company account and are not checked across regional '
                'endpoints.'
            ),
        ),
    ],
)


X402 = RailProfile(
    rail_id='x402',
    display_name='x402 protocol extensions',
    capabilities=[
        Capability(
            name='idempotent_replay', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation='x402-foundation/x402 @c9160a6 (main), specs/extensions/payment_identifier.md, Summary', url=_X402_PAYMENT_IDENTIFIER,
            quote=(
                'The `payment-identifier` extension enables clients to provide an `id` that serves as '
                'an idempotency key. Both resource servers and facilitators consume `PaymentPayload`, '
                'so this can be leveraged at either or both points in the stack to deduplicate requests '
                'and return cached responses for repeated submissions.'
            ),
            notes=(
                'Optional extension: a repeated id with the same payload returns the cached response '
                'and a different payload returns 409 Conflict. A server may leave it off (required '
                'defaults to false), and the scheme rules themselves rely on nonces.'
            ),
        ),
    ],
)


X402_EXACT = RailProfile(
    rail_id='x402_exact',
    display_name='x402 `exact` scheme (a fixed amount, no ceiling)',
    capabilities=[
        Capability(
            name='partial_debit', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation='x402-foundation/x402 @c9160a6 (main), specs/schemes/exact/scheme_exact.md, Summary', url=_X402_SCHEME_EXACT,
            quote=(
                '`exact` is a scheme that transfers a specific amount of funds from a client to a '
                'resource server. The resource server must know in advance the exact amount of funds '
                'they need to be transferred.'
            ),
            notes=(
                'The amount is fixed and known before the client signs, so there is no ceiling and no '
                'settle-less-than-signed option in this scheme. The EVM binding adds that the '
                "facilitator 'cannot modify the amount or destination'."
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation='x402-foundation/x402 @c9160a6 (main), specs/schemes/exact/scheme_exact_evm.md, Summary', url=_X402_SCHEME_EXACT_EVM,
            quote='In all cases, the Facilitator cannot modify the amount or destination.',
            notes=(
                'The signed amount is what moves; the facilitator only broadcasts the transaction. '
                'Several network bindings also state amount-exactness rules (scheme_exact.md, Critical '
                'Validation Requirements).'
            ),
        ),
        Capability(
            name='funds_held_in_customer_account', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/x402-specification-v2.md, 6.1 Payment Flow '
                'Models, table row authorization (default)'
            ), url=_X402_X402_SPECIFICATION_V2,
            quote=(
                'Read-only verify before the resource executes; funds move only after it completes '
                'successfully.'
            ),
            notes=(
                'This is the default flow the exact scheme uses: verify is read-only and value moves at '
                'the final settle, so nothing is held in between. Under the upfront flow the payment '
                "commits first and the exact spec says it 'defines no refund'."
            ),
        ),
    ],
)


X402_UPTO_EVM = RailProfile(
    rail_id='x402_upto_evm',
    display_name='x402 `upto` scheme, EVM (a Permit2 signature: a cap, no hold)',
    capabilities=[
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_evm.md, Phase 4 '
                'Settlement Logic, Settle-Time Verification example'
            ), url=_X402_SCHEME_UPTO_EVM,
            quote=(
                'In this example, the buyer signed for up to `20000` atomic units. The resource server '
                'consumed `1858` units of work. The facilitator verifies the signature against '
                '`permitted.amount` (`20000`), confirms `1858 <= 20000`, then transfers `1858` '
                'on-chain.'
            ),
            notes=(
                'The ceiling is a Permit2 signature and the resource server sets the settled amount at '
                'settle time, which may be far below the ceiling (1858 of 20000 in the example) or 0. '
                'The client does not choose the settled amount.'
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto.md, Core '
                'Properties (MUST) 4. Maximum Amount Enforcement'
            ), url=_X402_SCHEME_UPTO,
            quote='The settled amount MUST be less than or equal to the authorized maximum.',
            notes=(
                'Network-agnostic rule in the upto scheme; the EVM binding adds error code '
                'invalid_upto_evm_payload_settlement_exceeds_amount for an attempt to settle above the '
                'authorised amount. The settled amount may be 0.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto.md, Core '
                'Properties (MUST) 1. Single-Use Authorization'
            ), url=_X402_SCHEME_UPTO,
            quote=(
                'Each authorization MUST be settled at most once. After settlement (regardless of '
                'amount), the authorization is consumed and cannot be reused.'
            ),
            notes=(
                "The scheme rule says an authorization is consumed after settlement 'regardless of "
                "amount', while the EVM binding says a $0 settlement needs no on-chain transaction and "
                "the authorization 'simply expires unused'. Whether a $0 settlement consumes the "
                'Permit2 nonce is not stated.'
            ),
        ),
        Capability(
            name='funds_held_in_customer_account', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_evm.md, Security '
                'Considerations 5. Zero Settlement'
            ), url=_X402_SCHEME_UPTO_EVM,
            quote=(
                'Allowing $0 settlements means unused authorizations naturally expire without on-chain '
                'transactions, reducing gas costs and blockchain bloat.'
            ),
            notes=(
                'By necessary implication nothing is locked on-chain at authorization: an unused '
                "authorization lapses with no on-chain step. Verification reads the payer's balance and "
                'simulates a full-amount settle (Phase 3 steps 3 and 7); no locking step appears in the '
                'spec.'
            ),
        ),
        Capability(
            name='settled_amount_verifiable_against_usage', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_evm.md, Security '
                'Considerations 2. Server Trust'
            ), url=_X402_SCHEME_UPTO_EVM,
            quote=(
                'The `upto` scheme requires clients to trust that servers will charge fair amounts '
                'based on actual usage. Malicious servers could charge up to `amount` regardless of '
                'actual usage.'
            ),
            notes=(
                'New capability name settled_amount_verifiable_against_usage: can the payer verify from '
                'protocol data that the settled amount matches actual consumption. The settled amount '
                'itself is reported (SettlementResponse has amount and transaction), but the spec '
                "offers no usage evidence and says clients 'bear the risk of the full amount being "
                "charged'."
            ),
        ),
    ],
)


X402_UPTO_SVM = RailProfile(
    rail_id='x402_upto_svm',
    display_name='x402 `upto` scheme, Solana (the ceiling is escrowed)',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 5 Phase 4 '
                'Settlement'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote=(
                "Phase 3's `open` has already escrowed the ceiling, so the client is never charged "
                'before the resource runs, and the resource server determines the final charge only '
                'once execution completes.'
            ),
            notes=(
                'Unlike the EVM Permit2 binding, SVM upto escrows the full ceiling in an onchain '
                'payment channel before the resource runs. The verifier requires the deposit to equal '
                'maxAmount exactly.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 5 Phase 4 '
                'Settlement, application result determines the settled amount'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote=(
                'the resource server sets `paymentRequirements.amount` to the actual metered charge (`0 '
                '<= actual <= maxAmount`) and signs a `voucherSignature` for that amount.'
            ),
            notes=(
                'The actual charge may be anywhere from 0 up to the escrowed ceiling, and the server '
                'signs a voucher for it. The client signs only the channel open, not the charge.'
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 8 '
                'Security Properties, No overcharge'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote='Capped by the onchain `deposit`; verifier requires `deposit == maxAmount`.',
            notes=(
                'The ceiling is enforced by the escrow balance itself, not only by signature checks. A '
                'settle above maxAmount is rejected with '
                'invalid_upto_svm_payload_settlement_exceeds_amount.'
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 5 Phase 4 '
                'Settlement, Facilitator settlement procedure'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote=(
                '`distribute` is the instruction that pays `payTo`, refunds `deposit - actual` to the '
                'payer, closes the escrow token account, and advances the channel to its cleanup state.'
            ),
            notes=(
                'The unused part of the ceiling is refunded inside the same final settlement bundle '
                '(settle_and_seal then distribute). If the server never settles, the payer must call '
                'request_close and wait out the withdrawDelay grace period.'
            ),
        ),
        Capability(
            name='void_whole_hold', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 5 Phase 4 '
                'Settlement, Facilitator settlement procedure'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote=(
                'For the `actual == 0` refund path, `distribute` moves no funds to `payTo` and returns '
                'the full deposit to the client.'
            ),
            notes=(
                'A zero-amount claim settle releases the whole escrowed ceiling. The spec requires the '
                'server to settle this way when the resource fails after the deposit.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 9 Out of '
                'Scope'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote='`upto` settles at most once per authorization.',
            notes=(
                'Streaming and channels reused across many requests are sent to the batch-settlement '
                'scheme instead. One authorization is one channel that is sealed and distributed once.'
            ),
        ),
        Capability(
            name='settled_amount_verifiable_against_usage', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), specs/schemes/upto/scheme_upto_svm.md, 8 '
                'Security Properties, Metering trust'
            ), url=_X402_SCHEME_UPTO_SVM,
            quote=(
                'As in the generic `upto` spec, the client trusts the server to meter honestly within '
                'the ceiling.'
            ),
            notes=(
                'New capability name settled_amount_verifiable_against_usage: can the payer verify from '
                'protocol data that the settled amount matches actual consumption. The client signs '
                'only the channel open; the server signs the voucher that fixes the charge.'
            ),
        ),
    ],
)


X402_AUTH_CAPTURE = RailProfile(
    rail_id='x402_auth_capture',
    display_name='x402 `auth-capture` scheme (hold, capture, void, reclaim)',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture.md, Lifecycle operations table, '
                'authorize'
            ), url=_X402_SCHEME_AUTH_CAPTURE,
            quote="Reserves the client's funds, where they are held.",
            notes=(
                'Escrow flow: authorize places the hold before the resource runs, and the EVM binding '
                'keeps it in an onchain escrow contract. The alternative authorization flow places no '
                'hold, so capture, void and reclaim do not apply to it.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture.md, Summary'
            ), url=_X402_SCHEME_AUTH_CAPTURE,
            quote=(
                'it can be held before it is finalized, finalized for less than the maximum, cancelled '
                'outright, or returned after the fact.'
            ),
            notes=(
                'Capture may be for less than the held maximum, and the rest can be voided in the same '
                'settle via the optional voidAuthorizerSignature (EVM binding). See the '
                'remainder_auto_released row for what happens if no void is sent.'
            ),
        ),
        Capability(
            name='over_capture', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture.md, Core properties, Fund safety'
            ), url=_X402_SCHEME_AUTH_CAPTURE,
            quote=(
                'The amount settled is capped by the client-authorized maximum, and any fee is bounded '
                'by client-authorized limits.'
            ),
            notes=(
                "The EVM binding's capture precondition is 0 < amount <= capturableAmount, so a capture "
                'cannot exceed what is held. Fees are separately bounded by client-signed minFeeBps and '
                'maxFeeBps.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture_evm.md, Single-use enforcement'
            ), url=_X402_SCHEME_AUTH_CAPTURE_EVM,
            quote=(
                'Partial and repeated captures each get their own signature against the snapshot they '
                'expect.'
            ),
            notes=(
                "The scheme table marks capture as repeatable 'up to the held total'. Each capture "
                "consent is single-use because the escrow's payment state is the replay key."
            ),
        ),
        Capability(
            name='void_whole_hold', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture.md, Lifecycle operations table, void'
            ), url=_X402_SCHEME_AUTH_CAPTURE,
            quote='Releases the remaining hold back to the client.',
            notes=(
                'void releases whatever hold remains and is refused once nothing remains (EVM '
                'precondition capturableAmount > 0). It can follow a partial capture.'
            ),
        ),
        Capability(
            name='partial_void', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture_evm.md, Operator types, escrow ABI '
                'table'
            ), url=_X402_SCHEME_AUTH_CAPTURE_EVM,
            quote='`void` | `void(PaymentInfo paymentInfo)`',
            notes=(
                "The escrow's void takes no amount, so it releases the whole remaining hold; a hold "
                'shrinks only by capturing part of it. Voiding the remainder after a partial capture is '
                'supported.'
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture_evm.md, Lifecycle payloads, capture'
            ), url=_X402_SCHEME_AUTH_CAPTURE_EVM,
            quote=(
                '`voidAuthorizerSignature` is OPTIONAL and present only for a sync partial close-out: '
                'when set, this single `/settle` performs `capture` and then `void` on the remaining '
                'hold.'
            ),
            notes=(
                'A partial capture leaves the rest held unless a void is added, either atomically '
                "through the optional signature or as a separate void; the spec itself describes 'a "
                "partial that leaves the hold for later'. If no void is sent, the payer's own route to "
                'recover the hold is reclaim after the capture deadline.'
            ),
        ),
        Capability(
            name='incremental_authorization', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/auth-capture/scheme_auth_capture.md, Lifecycle operations table, '
                'authorize'
            ), url=_X402_SCHEME_AUTH_CAPTURE,
            quote=(
                "`authorize` | Reserves the client's funds, where they are held. | No — once per "
                'payment.'
            ),
            notes=(
                'authorize is once per payment and PaymentInfo, including maxAmount, is committed by '
                'paymentInfoHash. The lifecycle table lists no top-up operation.'
            ),
        ),
    ],
)


X402_BATCH_SETTLEMENT = RailProfile(
    rail_id='x402_batch_settlement',
    display_name='x402 `batch-settlement` scheme (a long-lived channel)',
    capabilities=[
        Capability(
            name='funds_held_in_customer_account', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Channel Lifecycle, '
                'Channel creation and deposits'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote=(
                'The client deposits funds from the `payer` address into an onchain escrow via one of '
                'two asset transfer methods'
            ),
            notes=(
                'Funds sit in a long-lived channel escrow rather than a per-payment hold. Balance minus '
                'totalClaimed is the unclaimed escrow that a refund or withdrawal can return.'
            ),
        ),
        Capability(
            name='partial_debit', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Summary'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote=(
                'the client authorizes a maximum per-request, and the server charges the actual cost '
                'within that ceiling.'
            ),
            notes=(
                'Dynamic pricing within a per-request maximum; each voucher carries a cumulative '
                'ceiling and the server tracks actual charges.'
            ),
        ),
        Capability(
            name='multiple_captures', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Channel Lifecycle, '
                'Requests and vouchers'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote=(
                'The server tracks a running total of actual charges per channel '
                '(`chargedCumulativeAmount`).'
            ),
            notes=(
                'Many claims accumulate against one deposit because vouchers are cumulative, and the '
                'server claims onchain at its discretion. Channels are long-lived and can be topped up '
                'after a refund.'
            ),
        ),
        Capability(
            name='partial_void', supported=True,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Client: Payment '
                'Construction, Refund Payload'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote='The optional `amount` requests a partial refund; omit it for a full refund.',
            notes=(
                'A partial cooperative refund of unclaimed escrow is possible but needs receiver-side '
                'consent. The unilateral route is a timed withdrawal after the withdrawDelay.'
            ),
        ),
        Capability(
            name='remainder_auto_released', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Security and Trust, 2. '
                'Withdrawal delay as escape hatch'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote=(
                'Cooperative refund returns unclaimed balance immediately when the server cooperates; '
                'timed withdrawal is the unilateral fallback.'
            ),
            notes=(
                'Unclaimed balance is not returned automatically: it takes a cooperative refund or a '
                'payer-initiated timed withdrawal. The spec is explicit that servers bear the risk of '
                'vouchers left unclaimed when the withdrawal finalizes.'
            ),
        ),
        Capability(
            name='settled_amount_verifiable_against_usage', supported=False,
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Security and Trust, 1. '
                'Capital risk and cumulative replay protection'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote='Over-claiming is a trust violation, not a protocol violation.',
            notes=(
                'New capability name settled_amount_verifiable_against_usage: can the payer verify from '
                'protocol data that the settled amount matches actual consumption. The receiver '
                'authorizer determines totalClaimed onchain within the signed ceiling, so an over-claim '
                'is not something the protocol rejects.'
            ),
        ),
    ],
    limits=[
        Limit(
            name='withdraw_delay_max_days', value=30, unit='days',
            source_tier=SourceTier.PRIMARY, obtained_on="2026-09-20",
            citation=(
                'x402-foundation/x402 @c9160a6 (main), '
                'specs/schemes/batch-settlement/scheme_batch_settlement_evm.md, Security and Trust, 2. '
                'Withdrawal delay as escape hatch'
            ), url=_X402_SCHEME_BATCH_SETTLEMENT_EVM,
            quote=(
                'The 15 min – 30 day bounds prevent a server from indefinitely trapping client funds '
                'while giving the server a fair window to claim outstanding vouchers.'
            ),
            notes=(
                "withdrawDelay must lie between 15 minutes and 30 days, so a payer's unilateral exit "
                'completes at least 15 minutes and at most 30 days after it starts. The 15-minute lower '
                'bound cannot be expressed in the allowed units.'
            ),
        ),
    ],
)


RAILS.update({r.rail_id: r for r in (VISA_CARD_AUTH, STRIPE_CARD_MANUAL_CAPTURE, ADYEN_CARD_AUTH, X402, X402_EXACT, X402_UPTO_EVM, X402_UPTO_SVM, X402_AUTH_CAPTURE, X402_BATCH_SETTLEMENT)})
