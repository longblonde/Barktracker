"use strict";
// Start-up: runs last, after every page file has registered itself.
Beat.start();
Theme.load();
pollStatus().finally(navigate);
setInterval(pollStatus, 2000);
