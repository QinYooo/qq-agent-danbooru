// Version the dependency too: the host only cache-busts the entry module.
import { createController, loadCharacters } from './gallery.js?v=1.4.4';
import { createRetriever, prepareDatabaseRuntime } from './retrieval.js?v=1.4.3';
import { createDatabaseView } from './database-view.js?v=1.4.3';

let controller;
export async function setup(api) {
  const { DATA_DIR } = await import('../../src/config.js');
  await prepareDatabaseRuntime(DATA_DIR);
  const index = await loadCharacters();
  const retrieve = createRetriever(index, api);
  controller = createController(api, index, { retrieve, listAll: retrieve.listAll, validate: retrieve.validate,
    prepare: retrieve.prepare, prepareReceiptRetry: retrieve.prepareReceiptRetry, overview: createDatabaseView() });
  api.log('角色、别名和画面描述由Agent规划数据库标签查询；全库随机保持直接抽图');
}
export function activate() { controller?.start(); }
export function deactivate() { controller?.stop(); }
export function dispose() { controller?.stop(); controller = undefined; }
export const hooks = {
  onMessage(ctx) { controller?.handle(ctx); }
};
// Existing host routing interface: a claimed command bypasses the chat model.
export const providers = {
  'dev.route': args => ({ handled: controller?.handled(args) === true })
};
