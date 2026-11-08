'use strict';

const { API_URL, STAGE_LABELS_EN } = require('../lib/constants');

// Hidden trigger: feeds the "Stage" dropdown of "Move lead to stage".
const perform = async (z) => {
  const response = await z.request({ url: `${API_URL}/stages` });
  const items = Array.isArray(response.data) ? response.data : [];
  return items
    .filter((stage) => stage && stage.id)
    .map((stage) => {
      const english = STAGE_LABELS_EN[stage.id] || stage.id;
      const portuguese = stage.label || '';
      return {
        id: stage.id,
        label: portuguese,
        name: portuguese && portuguese !== english ? `${english} (${portuguese})` : english,
      };
    });
};

module.exports = {
  key: 'stage_list',
  noun: 'Stage',
  display: {
    label: 'New Stage',
    description: 'Lists the HUMA IA funnel stages for dropdown menus.',
    hidden: true,
  },
  operation: {
    type: 'polling',
    perform,
    sample: { id: 'won', label: 'Fechado', name: 'Won (Fechado)' },
    outputFields: [
      { key: 'id', label: 'Stage ID' },
      { key: 'name', label: 'Stage' },
      { key: 'label', label: 'Stage name (Portuguese)' },
    ],
  },
};
