import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const code = await readFile(new URL('../desk/static/messages.js', import.meta.url), 'utf8');
const { timelineMessage, productCard, safeURL, buyerLabel } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

test('image messages render a thumbnail with preview action, not a squeezed placeholder bubble', () => {
  const html = timelineMessage({role:'agent',content:'【图片消息】',presentation:{kind:'image',body:'【图片消息】',media:[{kind:'image',url:'https://img.pddpic.com/a.jpg',inline_url:'https://img.pddpic.com/a.jpg'}]}});
  assert.match(html, /<img /);
  assert.match(html, /data-action="preview-message-image"/);
  assert.doesNotMatch(html, /class="bubble"/);
  assert.match(html, /media-fallback/);
});
test('product fields are escaped, title is clickable and missing prices are left empty', () => {
  const html = productCard({id:'123',name:'<img onerror=alert(1)>',image_url:null,url:'https://mobile.yangkeduo.com/goods.html?goods_id=123',price:''});
  assert.match(html, /&lt;img onerror/);
  assert.doesNotMatch(html, /<img onerror/);
  assert.doesNotMatch(html, /goods-price/);
  assert.match(html, /查看商品/);
});
test('private or lookalike media domains never become inline requests', () => {
  assert.equal(safeURL('https://127.0.0.1/x', true), '');
  assert.equal(safeURL('https://img.pddpic.com.evil.test/x', true), '');
  assert.equal(safeURL('javascript:alert(1)'), '');
});
test('withdrawn and system messages cannot display original hidden text', () => {
  const html = timelineMessage({role:'customer',content:'SECRET ORIGINAL',presentation:{kind:'withdrawn',body:'消息已撤回'}});
  assert.doesNotMatch(html, /SECRET ORIGINAL/);
  assert.match(html, /消息已撤回/);
});
test('names and order fields remain readable without fabricated values', () => {
  assert.equal(buyerLabel({buyer_uid:'123',buyer_name:'123'}), '买家（昵称未提供）');
  const html = productCard(null, {id:'ORDER1',status:'待发货',spec:'Large'});
  assert.match(html, /ORDER1/);
  assert.match(html, /待发货/);
  assert.doesNotMatch(html, /undefined|null/);
});

test('native client uploads and buyer uploads show previews even for old records missing inline_url', () => {
  const url = 'https://chat-img.pddugc.com/customer-service/photo.jpeg';
  assert.equal(safeURL(url, true), url);
  for (const role of ['agent', 'customer']) {
    const html = timelineMessage({role,content:'【图片消息】',presentation:{kind:'image',body:'【图片消息】',media:[{kind:'image',url,inline_url:null}]}});
    assert.match(html, /<img /);
    assert.match(html, /data-action="preview-message-image"/);
  }
  assert.equal(safeURL('https://chat-img.pddugc.com.evil.test/x', true), '');
  assert.equal(safeURL('https://other.pddugc.com/x', true), '');
});
