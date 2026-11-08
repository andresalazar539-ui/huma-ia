'use strict';

const { version } = require('./package.json');
const { version: platformVersion } = require('zapier-platform-core');

const authentication = require('./authentication');
const { addBearerHeader, handleErrors } = require('./lib/middleware');

const newLead = require('./triggers/new_lead');
const leadQualified = require('./triggers/lead_qualified');
const appointmentConfirmed = require('./triggers/appointment_confirmed');
const paymentApproved = require('./triggers/payment_approved');
const contractSigned = require('./triggers/contract_signed');
const contractRefused = require('./triggers/contract_refused');
const conversationList = require('./triggers/conversation_list');
const stageList = require('./triggers/stage_list');

const sendMessage = require('./creates/send_message');
const moveStage = require('./creates/move_stage');
const addNote = require('./creates/add_note');
const handOff = require('./creates/hand_off');

const findConversation = require('./searches/find_conversation');

const byKey = (items) => Object.fromEntries(items.map((item) => [item.key, item]));

module.exports = {
  version,
  platformVersion,

  // Inputs are trimmed and validated inside each action.
  flags: { cleanInputData: false },

  authentication,
  beforeRequest: [addBearerHeader],
  afterResponse: [handleErrors],

  triggers: byKey([
    newLead,
    leadQualified,
    appointmentConfirmed,
    paymentApproved,
    contractSigned,
    contractRefused,
    conversationList,
    stageList,
  ]),

  creates: byKey([sendMessage, moveStage, addNote, handOff]),

  searches: byKey([findConversation]),
};
