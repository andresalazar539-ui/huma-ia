import { createHmac, timingSafeEqual } from 'crypto';

/**
 * HUMA IA signs every delivery with the hook secret:
 *   X-HUMA-Timestamp: <timestamp>
 *   X-HUMA-Signature: sha256=<hex HMAC-SHA256(secret, timestamp + "." + raw body)>
 */
export function computeSignature(secret: string, timestamp: string, rawBody: Buffer | string): string {
	const hmac = createHmac('sha256', secret);
	hmac.update(`${timestamp}.`);
	hmac.update(rawBody);
	return `sha256=${hmac.digest('hex')}`;
}

/** Constant-time check of the X-HUMA-Signature header. */
export function isValidSignature(
	secret: string,
	timestamp: string,
	rawBody: Buffer | string,
	signatureHeader: string,
): boolean {
	if (!secret || !timestamp || !signatureHeader) return false;
	const expected = Buffer.from(computeSignature(secret, timestamp, rawBody), 'utf8');
	const received = Buffer.from(signatureHeader.trim().toLowerCase(), 'utf8');
	if (expected.length !== received.length) return false;
	return timingSafeEqual(expected, received);
}
