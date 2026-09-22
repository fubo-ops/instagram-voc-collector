'use strict';
chrome.runtime.sendMessage({action: 'wake'}).catch(() => {});
