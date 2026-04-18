window.fmCopy = async function (value, label = 'Copied') {
  try {
    await navigator.clipboard.writeText(value);
    window.alert(label);
  } catch (err) {
    window.prompt('Copy this value', value);
  }
};

window.fmPostCommand = async function (machineId, command) {
  if (!confirm(`Queue ${command} for ${machineId}?`)) return;
  const response = await fetch(`/tenant/command/${machineId}?cmd=${encodeURIComponent(command)}`, { method: 'POST' });
  const data = await response.json();
  alert(data.message || 'Command queued');
  window.location.reload();
};

window.fmAckAlert = async function (alertId) {
  const response = await fetch(`/tenant/alerts/${alertId}/ack`, { method: 'POST' });
  const data = await response.json();
  if (data.ok) window.location.reload();
};

window.fmLockGlobal = async function (machineId) {
  if (!confirm(`Queue global isolate for ${machineId}?`)) return;
  await fetch(`/api/trigger_lockdown/${machineId}`, { method: 'POST' });
  alert('Isolation queued');
};

window.startPayment = async function (tier, keyId, baseUrl) {
  if (!keyId) {
    alert('Razorpay is not configured yet. Add the Razorpay environment variables first.');
    return;
  }
  const email = window.prompt('Enter the email address for this license');
  if (!email) return;

  const orderResponse = await fetch('/payment/create-order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tier, email })
  });
  const orderData = await orderResponse.json();
  if (!orderResponse.ok) {
    alert(orderData.error || 'Could not create payment order');
    return;
  }

  const options = {
    key: keyId,
    order_id: orderData.order_id,
    name: 'FMSecure',
    description: orderData.description || 'FMSecure license purchase',
    amount: orderData.amount,
    currency: orderData.currency || 'INR',
    handler: async function (response) {
      const verifyResponse = await fetch('/payment/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          razorpay_order_id: response.razorpay_order_id,
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_signature: response.razorpay_signature
        })
      });
      const verifyData = await verifyResponse.json();
      if (!verifyResponse.ok || !verifyData.success) {
        alert(verifyData.error || 'Payment verification failed');
        return;
      }
      const redirect = new URL('/payment/success', baseUrl || window.location.origin);
      redirect.searchParams.set('key', verifyData.license_key || '');
      redirect.searchParams.set('email', email);
      redirect.searchParams.set('tier', tier);
      window.location.href = redirect.toString();
    },
    prefill: { email }
  };

  if (!window.Razorpay) {
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.onload = () => new window.Razorpay(options).open();
    document.body.appendChild(script);
    return;
  }
  new window.Razorpay(options).open();
};
