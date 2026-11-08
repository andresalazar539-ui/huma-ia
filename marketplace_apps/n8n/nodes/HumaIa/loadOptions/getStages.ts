import type { ILoadOptionsFunctions, INodePropertyOptions } from 'n8n-workflow';
import { humaApiRequest } from '../shared/transport';

type StageItem = {
	id?: string;
	label?: string;
};

const STAGE_NAMES: Record<string, string> = {
	discovery: 'Discovery',
	offer: 'Offer',
	closing: 'Closing',
	committed: 'Committed',
	qualified: 'Qualified',
	won: 'Won',
	lost: 'Lost',
};

export async function getStages(this: ILoadOptionsFunctions): Promise<INodePropertyOptions[]> {
	const response = (await humaApiRequest.call(this, 'GET', '/stages')) as StageItem[];
	return (Array.isArray(response) ? response : [])
		.filter((stage) => stage.id)
		.map((stage) => {
			const id = stage.id as string;
			return {
				name: STAGE_NAMES[id] ?? stage.label ?? id,
				value: id,
				description: stage.label ? `Shown in HUMA IA as "${stage.label}"` : undefined,
			};
		});
}
