/* 需求狀態的單一說法來源。

   先前三個畫面各有一份對照表，同一個狀態被叫成三種名字：
   suggested 在工作區是「待核准」、在後台是「待確認」；matched 在工作區是
   「執行中」、在後台是「已媒合」。調度者在畫面之間切換時，同一筆需求會換
   一個詞，只能自己猜那是不是同一件事（Nielsen #4 一致性）。

   這份要跟後端的 app/labels.py 一致，tests/test_status_labels.py 會核對。
   WHY 欄是「接下來誰要動作」，拿來當 tooltip，省掉另外寫一份說明文件。 */
window.NEED_STATUS_LABEL = {
  open:      '待媒合',
  suggested: '待核准',
  matched:   '執行中',
  fulfilled: '已完成',
  cancelled: '已取消',
};

window.NEED_STATUS_WHY = {
  open:      '還沒有配對到物資，等待系統媒合或管理員手動指派。',
  suggested: '系統已經配好物資，等管理員核准後才會通知志工。',
  matched:   '已派給志工，志工正在處理，完成後會回報。',
  fulfilled: '志工已回報送達，這筆需求結束。',
  cancelled: '已取消，不會再派送。',
};

window.needStatusLabel = function (value) {
  return window.NEED_STATUS_LABEL[value] || value || '未知';
};

/* 警報類型。後端送四種，總覽頁原本只認三種，unwell 沒收錄就直接把英文字串
   印在警報列上給調度者看。同樣由 tests/test_status_labels.py 核對兩邊一致。 */
window.ALERT_TYPE_LABEL = {
  help_needed:    '主動求助',
  unwell:         '身體不舒服',
  no_response_1h: '超過 1 小時未回應',
  no_response_3h: '超過 3 小時未回應',
};

window.alertTypeLabel = function (value) {
  return window.ALERT_TYPE_LABEL[value] || value || '未知';
};
