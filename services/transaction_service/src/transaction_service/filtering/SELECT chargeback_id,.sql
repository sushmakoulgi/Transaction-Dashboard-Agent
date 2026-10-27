SELECT chargeback_id,
       transaction_id,
       customer_id,
       reason_code,
       chargeback_amount,
       chargeback_status,
       opened_at,
       dispute_details
FROM public.chargebacks
LIMIT 1000;