const form = document.querySelector('#redeem');
const result = document.querySelector('#result');
const statusEl = document.querySelector('#status');
const detailEl = document.querySelector('#detail');
const noteEl = document.querySelector('#status-note');
let current = null;
let timer = null;

/* 文案解释"现在发生什么"和"你该做什么"，而不是复述状态名。 */
const notes = {
    queued: '订单已受理，正在等待处理。可以保留此页面，或稍后重新输入兑换信息查询。',
    checking: '正在核验账号资格，通常几秒内完成。',
    link_created: '付款链接已创建，正在等待完成交付。',
    awaiting_payment: '已进入等待付款阶段，请联系运营人员确认后续步骤。',
    payment_ready: '付款已准备就绪，正在提交。',
    payment_submitted: '付款已提交，正在等待结果确认。请勿重复提交。',
    succeeded: '交付完成。模拟演示不会产生真实赠送。',
    failed: '订单明确失败，请联系运营人员核实原因。',
    review: '结果仍待核实。请勿重复购买或再次付款，联系运营人员处理。',
    cancelled: '此订单已取消。',
};

/* 终态：不再轮询；review 也停下，等人工处理。 */
const terminalStatuses = ['succeeded', 'failed', 'review', 'cancelled'];

const IN_FLIGHT = ['queued', 'checking', 'link_created', 'awaiting_payment',
                   'payment_ready', 'payment_submitted'];

function clearErrors() {
    for (const field of ['code', 'username']) {
        const input = document.querySelector('#' + field);
        const error = document.querySelector('#' + field + '-error');
        error.textContent = '';
        input.removeAttribute('aria-invalid');
    }
}

function showErrors(errors) {
    for (const [field, messages] of Object.entries(errors || {})) {
        const input = document.querySelector('#' + field);
        const error = document.querySelector('#' + field + '-error');
        if (input) input.setAttribute('aria-invalid', 'true');
        if (error) error.textContent = messages.join(' ');
    }
}

async function refresh() {
    if (!current) return;
    clearTimeout(timer);
    try {
        const response = await fetch('/api/orders/' + current.id, {
            headers: { Authorization: 'Bearer ' + current.access_token },
        });
        if (!response.ok) throw Error('暂时无法查询，请稍后刷新，或重新输入兑换信息。');
        const order = await response.json();

        statusEl.textContent = order.status_label;
        detailEl.textContent = '@' + order.username + ' · ' + order.plan;
        noteEl.textContent =
            order.status === 'succeeded' && order.mode === 'live'
                ? '礼遇已成功交付。'
                : notes[order.status] || '订单处理中，请耐心等待。';

        /* 进行中时给状态点加脉冲，让"系统正在工作"可见 */
        result.dataset.live = IN_FLIGHT.includes(order.status) ? '1' : '0';
        if (IN_FLIGHT.includes(order.status)) timer = setTimeout(refresh, 5000);
    } catch (error) {
        noteEl.textContent = error.message;
        result.dataset.live = '0';
    }
}

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.querySelector('#submit');
    clearErrors();
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch('/api/redeem', {
            method: 'POST',
            body: new FormData(form),
        });
        const data = await response.json();
        if (!response.ok) {
            showErrors(data.errors);
            const first = document.querySelector('[aria-invalid="true"]');
            if (first) first.focus();
            return;
        }
        current = data;
        result.hidden = false;
        await refresh();
    } catch (error) {
        showErrors({ code: ['网络暂时不可用，请稍后再试。'] });
    } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
    }
});

document.querySelector('#refresh').addEventListener('click', refresh);
