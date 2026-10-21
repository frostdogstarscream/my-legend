const KEY='my-legend:demo-wallet:v1'
export interface DemoWallet { balance:number; ownedProductIds:string[] }
export function readDemoWallet():DemoWallet { const value=uni.getStorageSync(KEY);return value&&Number.isFinite(value.balance)&&value.balance>=0&&Array.isArray(value.ownedProductIds)?value:{balance:1250,ownedProductIds:[]} }
export function redeemDemoProduct(id:string):DemoWallet {if(id!=='demo-sku-001')throw new Error('商品不存在');const wallet=readDemoWallet();if(wallet.ownedProductIds.includes(id))return wallet;if(wallet.balance<300)throw new Error('灵感币不足');const next={balance:wallet.balance-300,ownedProductIds:[...wallet.ownedProductIds,id]};uni.setStorageSync(KEY,next);return next}
