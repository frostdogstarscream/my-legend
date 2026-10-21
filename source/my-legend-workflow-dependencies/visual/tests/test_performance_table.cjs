const {test} = require('node:test');
const assert = require('node:assert/strict');
const {build, timestamp} = require('../app/static/performance-table.js');
const fixture = () => ({timeline_status:'estimated', actors:[{actor_id:'a', character_name:'刘备'}], events:[
  {type:'light',start_ms:0,end_ms:100},
  {type:'speech',actor_id:'a',text:'先生，请留步。',start_ms:1500,end_ms:64200,timeline_status:'estimated',performance:{emotion:{primary:'serious',secondary:'sad',intensity:0.78},prosody:{rate:0.9}},actions:[{action:'bow',start_offset_ms:300,duration_ms:900}]}
]});
test('毫秒精度、跨分钟与无效时间', () => {
  assert.equal(timestamp(0),'00:00.000');
  assert.equal(timestamp(64200),'01:04.200');
  assert.equal(timestamp(null),'未提供');
  assert.equal(timestamp(-10),'未提供');
});
test('只展示台词，中文字段及时间与原协议一致', () => {
  const data=fixture(), original=JSON.stringify(data), view=build(data);
  for(const value of ['刘备','先生，请留步。','00:01.500','01:04.200','严肃 / 悲伤','78%','0.90 倍','鞠躬','0.30 秒','0.90 秒']) assert.ok(view.rows.includes(value),value);
  assert.equal((view.rows.match(/<tr>/g)||[]).length,1);
  assert.ok(view.summary.includes('估算'));
  assert.equal(JSON.stringify(data),original);
});
test('空协议和缺失字段不导致渲染失败', () => {
  for(const data of [null,undefined,{}, {events:[]},{events:[{type:'light'}]}]) assert.ok(build(data).rows.includes('暂无台词数据'));
  assert.ok(build({events:[{type:'speech'}]}).rows.includes('未提供'));
});
test('未知标签安全降级，台词不能注入 HTML', () => {
  const data=fixture(); data.events[1].text='<img src=x onerror=alert(1)>';
  data.events[1].performance.emotion.primary='alien'; data.events[1].actions=[{action:'unknown'}];
  const view=build(data); assert.ok(view.rows.includes('&lt;img')); assert.ok(!view.rows.includes('<img'));
  assert.ok(view.rows.includes('未识别情绪')); assert.ok(view.rows.includes('未识别动作'));
});
test('锁定状态必须顶层与所有语音一致，零强度不可视为缺失', () => {
  const data=fixture(); data.timeline_status='locked';
  assert.ok(build(data).summary.includes('估算'));
  data.events[1].timeline_status='locked'; data.events[1].performance.emotion.intensity=0;
  assert.ok(build(data).summary.includes('已锁定')); assert.ok(build(data).rows.includes('0%'));
});
